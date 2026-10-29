{
  description = "OMP Lite, unpacked into the Nix store";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";

  outputs = { self, nixpkgs }:
    let
      version = "18.13.2";
      release = "omp-v${version}-r5";
      targets = {
        aarch64-darwin = {
          target = "darwin-arm64";
          sha256 = "916b0811519f3c8b7a1a5c6bb48897209b52d4110b21c986f3f94ea6ab3017a6";
        };
        x86_64-darwin = {
          target = "darwin-x64";
          sha256 = "69632418013342d0b6e2caf116bcc4ec13af780669dbf054ad7a44221bcdeeef";
        };
        aarch64-linux = {
          target = "linux-arm64";
          sha256 = "f2dec7ea74b54a6a653f5f1f6eaf47d5e391b2ebc8cc2132ab3a0aec288b1691";
        };
        x86_64-linux = {
          target = "linux-x64";
          sha256 = "899f46d613c738eff3994c3a68065041e1fa2cba57ff1dcf4b111d70ff0eaf14";
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
          omp = stdenv.mkDerivation {
            pname = "omp-lite";
            inherit version;
            src = pkgs.fetchurl {
              url = "https://github.com/nikvdp/omp-portable/releases/download/${release}/omp-lite-${version}-${target}";
              inherit sha256;
            };
            # patchelf 0.15.2 relocates x64 libvips's .init/.plt without fixing
            # their references. 0.18 preserves them when growing the RPATH.
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
              ${lib.optionalString stdenv.hostPlatform.isLinux ''
                # Both libc variants are shipped; glibc Bun must not pick musl's libvips.
                rm -r "$out/libexec/omp-lite/runtime/node_modules/@img/"*linuxmusl*
              ''}
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
              "$out/libexec/omp-lite/bun" --no-env-file -e '
                const sharp = require(process.argv[1]);
                const png = await sharp({ create: { width: 2, height: 3, channels: 3,
                  background: "#ff0000" } }).png().toBuffer();
                const metadata = await sharp(png).metadata();
                if (metadata.width !== 2 || metadata.height !== 3 || metadata.format !== "png")
                  throw new Error("Native image round-trip failed");
                console.log("native image round-trip: ok");
              ' "$out/libexec/omp-lite/runtime/node_modules/sharp"
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
