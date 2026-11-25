{
  description = "OMP Lite, unpacked into the Nix store";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";

  outputs = { self, nixpkgs }:
    let
      pinned = builtins.fromJSON (builtins.readFile ./config/nix-release.json);
      inherit (pinned) version release;
      targets = {
        aarch64-darwin = {
          target = "darwin-arm64";
          sha256 = pinned.hashes.darwin-arm64;
        };
        x86_64-darwin = {
          target = "darwin-x64";
          sha256 = pinned.hashes.darwin-x64;
        };
        aarch64-linux = {
          target = "linux-arm64";
          sha256 = pinned.hashes.linux-arm64;
        };
        x86_64-linux = {
          target = "linux-x64";
          sha256 = pinned.hashes.linux-x64;
        };
      };
      forAllSystems = nixpkgs.lib.genAttrs (builtins.attrNames targets);
    in
    {
      packages = forAllSystems (system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          inherit (pkgs) lib stdenv;
          inherit (targets.${system}) target sha256;
          python = pkgs.python3.withPackages (p: [ p.zstandard ]);
          omp = pkgs.stdenvNoCC.mkDerivation {
            pname = "omp-lite";
            inherit version;
            src = pkgs.fetchurl {
              url = "https://github.com/nikvdp/omp-portable/releases/download/${release}/omp-lite-${version}-${target}";
              inherit sha256;
            };
            nativeBuildInputs = [ python pkgs.patchelfUnstable ]
              ++ lib.optionals stdenv.hostPlatform.isLinux [ pkgs.autoPatchelfHook ];
            buildInputs = lib.optionals stdenv.hostPlatform.isLinux [
              stdenv.cc.cc.lib
            ];
            dontUnpack = true;
            dontConfigure = true;
            dontBuild = true;
            dontStrip = true;
            dontPatchELF = stdenv.hostPlatform.isDarwin;
            strictDeps = true;

            installPhase = ''
              runHook preInstall
              python ${./scripts/extract_lite.py} "$src" unpacked ${target} ${version}
              mkdir -p "$out/libexec/omp-lite" "$out/bin"
              cp -R unpacked/runtime unpacked/licenses "$out/libexec/omp-lite/"
              install -m755 unpacked/bin/omp.real "$out/libexec/omp-lite/bun"
              cat > "$out/bin/omp" <<'EOF'
              #!${pkgs.bash}/bin/bash
              args=("$@")
              for ((i=0; i<''${#args[@]}; i++)); do
                case "''${args[i]}" in
                  --profile) ((i+=1));;
                  --profile=*) ;;
                  update)
                    printf '%s\n' 'OMP is installed with Nix. Update the flake input or install a newer flake revision to update OMP.'
                    exit 0;;
                  *) break;;
                esac
              done
              unset PI_COMPILED PI_BUNDLED
              export PATH="$out/bin''${PATH:+:$PATH}"
              if [[ "''${HERDR_ENV:-}" == 1 && "$(uname -s)" == Darwin ]]; then
                exec -a omp "$out/libexec/omp-lite/bun" --no-env-file \
                  "$out/libexec/omp-lite/runtime/node_modules/@oh-my-pi/pi-coding-agent/src/cli.ts" "$@"
              fi
              exec "$out/libexec/omp-lite/bun" --no-env-file \
                "$out/libexec/omp-lite/runtime/node_modules/@oh-my-pi/pi-coding-agent/src/cli.ts" "$@"
              EOF
              substituteInPlace "$out/bin/omp" --replace-fail '$out' "$out"
              chmod +x "$out/bin/omp"
              runHook postInstall
            '';

            doInstallCheck = true;
            installCheckPhase = ''
              runHook preInstallCheck
              cp ${./scripts/extract_lite.py} extract_lite.py
              PYTHONPATH="$PWD" python ${./tests/test_extract_lite.py}
              export HOME="$TMPDIR/omp-home"
              mkdir -p "$HOME"
              actual=$("$out/bin/omp" --version)
              [[ "$actual" == "omp/${version}" ]]
              "$out/bin/omp" --smoke-test
              mkdir -p "$HOME/.omp/agent/extensions"
              cat > sdk-probe.ts <<'EOF'
              import { writeFileSync } from "node:fs";
              import { AgentRegistry } from "@oh-my-pi/pi-coding-agent";
              import { resetYieldTurnState } from "@oh-my-pi/pi-coding-agent/tools/yield";
              ${if lib.versionAtLeast version "19.2.0" then ''
                import { isUiNode, jsx } from "@oh-my-pi/pi-tui";
                import { visibleWidth } from "@oh-my-pi/pi-tui/text/width";
                const checkTui = () => {
                  const node = jsx("text", { text: "界" });
                  return isUiNode(node) && visibleWidth(node.props().text) === 2;
                };
              '' else ''
                import { assembleYieldResult } from "@oh-my-pi/pi-tui/tools/task-yield-assembly";
                const checkTui = () => typeof assembleYieldResult === "function";
              ''}
              export default function(pi) {
                if (AgentRegistry !== pi.pi.AgentRegistry || typeof resetYieldTurnState !== "function" || !checkTui())
                  throw new Error("OMP extension SDK mismatch");
                writeFileSync(process.env.OMP_NIX_PROBE, "ok\n");
              }
              EOF
              ln -s "$PWD/sdk-probe.ts" "$HOME/.omp/agent/extensions/sdk-probe.ts"
              export OMP_NIX_PROBE="$PWD/sdk-probe-result"
              "$out/bin/omp" models ls --json > models.json
              test "$(cat "$OMP_NIX_PROBE")" = ok
              "$out/libexec/omp-lite/bun" --no-env-file -e '
                const source = Buffer.from(
                  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a2ioAAAAASUVORK5CYII=", "base64");
                const png = await new Bun.Image(source).resize(2, 3).png().bytes();
                const metadata = await new Bun.Image(png).metadata();
                if (metadata.width !== 2 || metadata.height !== 3 || metadata.format !== "png")
                  throw new Error("Native image round-trip failed");
                console.log("native image round-trip: ok");
              '
              runHook postInstallCheck
            '';

            meta = {
              description = "OMP coding agent with its unpacked Lite runtime";
              homepage = "https://github.com/nikvdp/omp-portable";
              license = lib.licenses.mit;
              sourceProvenance = [ lib.sourceTypes.binaryNativeCode ];
              platforms = builtins.attrNames targets;
              mainProgram = "omp";
            };
          };
        in
        {
          inherit omp;
          default = omp;
        });

      apps = forAllSystems (system: {
        default = {
          type = "app";
          program = "${self.packages.${system}.default}/bin/omp";
          meta.description = "Run unpacked OMP Lite";
        };
        omp = self.apps.${system}.default;
      });

      checks = forAllSystems (system: {
        omp = self.packages.${system}.default;
      });
    };
}
