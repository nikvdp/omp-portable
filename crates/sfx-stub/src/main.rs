#![cfg(unix)]
use anyhow::{bail, ensure, Context, Result};
use fs2::FileExt;
use sfx_format::{digest, hash, safe_path, validate_manifest, Footer, Manifest};
use std::{
    collections::BTreeSet,
    env,
    ffi::OsString,
    fs::{self, File, OpenOptions},
    io::{Read, Seek, SeekFrom, Write},
    os::unix::{
        fs::{OpenOptionsExt, PermissionsExt},
        process::CommandExt,
    },
    path::{Path, PathBuf},
    process::Command,
};
fn regular(p: &Path) -> Result<fs::Metadata> {
    let m = fs::symlink_metadata(p)?;
    ensure!(m.is_file(), "not a regular file: {}", p.display());
    Ok(m)
}
fn verify(root: &Path) -> Result<Manifest> {
    regular(&root.join("manifest.json"))?;
    let m: Manifest = serde_json::from_reader(File::open(root.join("manifest.json"))?)?;
    validate_manifest(&m)?;
    ensure!(
        m.target == sfx_format::native_target(),
        "payload target does not match this launcher"
    );
    for (p, r) in &m.files {
        let p = root.join(p);
        let meta = regular(&p)?;
        ensure!(
            meta.len() == r.size && digest(File::open(&p)?)? == r.sha256,
            "cached component mismatch: {}",
            p.display()
        );
        ensure!(
            (meta.permissions().mode() & 0o111 != 0) == r.executable,
            "incorrect executable permissions"
        );
    }
    Ok(m)
}
fn private_dir(p: &Path) -> Result<()> {
    fs::create_dir_all(p)?;
    ensure!(
        fs::symlink_metadata(p)?.is_dir(),
        "cache directory is a symlink or not a directory"
    );
    fs::set_permissions(p, fs::Permissions::from_mode(0o700))?;
    Ok(())
}
fn extract(exe: &Path) -> Result<PathBuf> {
    let mut f = File::open(exe)?;
    let footer = Footer::read(&mut f)?;
    // Verify even when READY exists: an altered artifact must never reuse a good cache.
    f.seek(SeekFrom::Start(footer.offset))?;
    ensure!(
        hash((&mut f).take(footer.length))? == footer.digest,
        "payload SHA-256 mismatch"
    );
    let id = footer
        .digest
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect::<String>();
    let home = PathBuf::from(env::var_os("HOME").context("HOME is not set")?);
    #[cfg(target_os = "macos")]
    let cache = home.join("Library/Caches");
    #[cfg(not(target_os = "macos"))]
    let cache = env::var_os("XDG_CACHE_HOME")
        .filter(|v| !v.is_empty())
        .map(PathBuf::from)
        .unwrap_or(home.join(".cache"));
    ensure!(cache.is_absolute(), "cache root must be absolute");
    let base = cache.join("omp-portable/dist");
    private_dir(&base)?;
    let lock = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW)
        .open(base.join(format!("{id}.lock")))?;
    lock.lock_exclusive()?;
    let root = base.join(&id);
    if root.exists() {
        ensure!(fs::symlink_metadata(&root)?.is_dir(), "invalid cache root");
        ensure!(
            fs::read_to_string(root.join("READY"))? == id,
            "invalid READY marker; remove this distribution cache and retry"
        );
        verify(&root)?;
        FileExt::unlock(&lock)?;
        return Ok(root);
    }
    for e in fs::read_dir(&base)? {
        let e = e?;
        if e.file_name()
            .to_string_lossy()
            .starts_with(&format!("{id}.tmp."))
        {
            if e.file_type()?.is_dir() {
                fs::remove_dir_all(e.path())?
            } else {
                fs::remove_file(e.path())?
            }
        }
    }
    let tmp = base.join(format!("{id}.tmp.{}", std::process::id()));
    private_dir(&tmp)?;
    let result = (|| -> Result<()> {
        f.seek(SeekFrom::Start(footer.offset))?;
        let dec = zstd::stream::read::Decoder::new((&mut f).take(footer.length))?;
        let mut ar = tar::Archive::new(dec);
        let mut seen = BTreeSet::new();
        let mut expanded = 0u64;
        for item in ar.entries()? {
            let mut entry = item?;
            let p = entry.path()?.into_owned();
            safe_path(&p)?;
            ensure!(
                entry.header().entry_type().is_file(),
                "only regular payload files are supported"
            );
            ensure!(seen.insert(p.clone()), "duplicate payload path");
            ensure!(p != Path::new("READY"), "reserved payload path");
            expanded = expanded
                .checked_add(entry.size())
                .context("expanded size overflow")?;
            ensure!(
                expanded < 8 * 1024 * 1024 * 1024,
                "expanded payload too large"
            );
            if p == Path::new("manifest.json") {
                ensure!(entry.size() < 8 * 1024 * 1024, "manifest too large");
            }
            let dest = tmp.join(&p);
            fs::create_dir_all(dest.parent().context("missing parent")?)?;
            let mut out = OpenOptions::new()
                .write(true)
                .create_new(true)
                .mode(0o600)
                .open(&dest)?;
            std::io::copy(&mut entry, &mut out)?;
            let mode = if entry.header().mode()? & 0o111 != 0 {
                0o755
            } else {
                0o644
            };
            out.set_permissions(fs::Permissions::from_mode(mode))?;
            out.sync_all()?;
        }
        // Drain decoder so truncated/trailing compressed data is not hidden by tar EOF.
        std::io::copy(&mut ar.into_inner(), &mut std::io::sink())?;
        let m = verify(&tmp)?;
        let expected: BTreeSet<_> = m
            .files
            .keys()
            .map(PathBuf::from)
            .chain(std::iter::once(PathBuf::from("manifest.json")))
            .collect();
        ensure!(seen == expected, "unmanifested payload files");
        let mut ready = File::create(tmp.join("READY"))?;
        ready.write_all(id.as_bytes())?;
        ready.sync_all()?;
        File::open(&tmp)?.sync_all()?;
        fs::rename(&tmp, &root)?;
        File::open(&base)?.sync_all()?;
        Ok(())
    })();
    if let Err(e) = result {
        let _ = fs::remove_dir_all(tmp);
        return Err(e);
    }
    FileExt::unlock(&lock)?;
    Ok(root)
}
fn profiles(args: &[OsString]) -> BTreeSet<String> {
    // Precreate only safe candidate names. Upstream remains authoritative for argv
    // parsing: a --profile token can also be a tool's argument or a prompt value.
    // Include the environment candidate even when a token is only a literal.
    let mut candidates = vec![env::var("OMP_PROFILE")
        .ok()
        .or_else(|| env::var("PI_PROFILE").ok())
        .unwrap_or_default()];
    let mut i = 0;
    while i < args.len() {
        if args[i] == "--" {
            break;
        }
        if args[i] == "--profile" {
            if let Some(v) = args.get(i + 1).and_then(|x| x.to_str()) {
                candidates.push(v.into())
            }
        } else if let Some(v) = args[i].to_str().and_then(|a| a.strip_prefix("--profile=")) {
            candidates.push(v.into())
        }
        i += 1;
    }
    candidates
        .into_iter()
        .map(|p| p.trim().to_string())
        .filter(|p| {
            if p.is_empty() || p == "default" {
                return false;
            }
            let b = p.as_bytes();
            let reserved = p.split('.').next().unwrap_or("").to_ascii_uppercase();
            p.len() <= 64
                && b[0].is_ascii_alphanumeric()
                && b.iter()
                    .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || b"._-".contains(c))
                && !p.ends_with('.')
                && !["CON", "PRN", "AUX", "NUL"].contains(&reserved.as_str())
                && !(reserved.len() == 4
                    && (reserved.starts_with("COM") || reserved.starts_with("LPT"))
                    && reserved.as_bytes()[3].is_ascii_digit())
        })
        .collect()
}
fn update(args: &[OsString]) -> bool {
    // Upstream strips profile selectors before subcommand routing.
    let mut i = 0;
    while i < args.len() {
        if args[i] == "--profile" {
            i += 2;
            continue;
        }
        if args[i]
            .to_str()
            .is_some_and(|a| a.starts_with("--profile="))
        {
            i += 1;
            continue;
        }
        return args[i] == "update";
    }
    false
}
fn run() -> Result<()> {
    let mut process_args = env::args_os();
    let argv0 = process_args.next().context("missing argv[0]")?;
    let args: Vec<_> = process_args.collect();
    let exe = env::current_exe()?;
    // Private shims are copies of this executable. Derive their root from the
    // executable, but dispatch by argv[0] so every helper copy uses one binary.
    let exe_name = exe.file_name().and_then(|name| name.to_str());
    let is_shim = exe.parent().and_then(Path::file_name)
        == Some(std::ffi::OsStr::new("launcher-bin"))
        && exe_name.is_some_and(|name| {
            ["omp", "python", "python3", "trafilatura", "chromium"].contains(&name)
        });
    let (root, manifest) = if is_shim {
        let root = exe
            .parent()
            .and_then(Path::parent)
            .context("invalid shim path")?
            .to_path_buf();
        let manifest = verify(&root)?;
        (root, manifest)
    } else {
        let root = extract(&exe)?;
        let manifest: Manifest = serde_json::from_reader(File::open(root.join("manifest.json"))?)?;
        (root, manifest)
    };
    let invocation = if is_shim {
        Path::new(&argv0)
            .file_name()
            .and_then(|name| name.to_str())
            .filter(|name| ["omp", "python", "python3", "trafilatura", "chromium"].contains(name))
            .or(exe_name)
            .context("invalid shim name")?
    } else {
        "omp"
    };
    if invocation == "omp" && update(&args) {
        println!("This is a portable OMP build.\nRebuild with this builder or download a newer portable artifact to update OMP.\nAll update variants, including --plugins and --check, are blocked in this milestone.");
        return Ok(());
    }

    let xdg = root.join("xdg-cache");
    let mut cmd = if invocation == "omp" {
        fs::create_dir_all(xdg.join("omp"))?;
        for p in profiles(&args) {
            fs::create_dir_all(xdg.join("omp/profiles").join(p))?;
        }
        let mut cmd = Command::new(root.join("bin/omp.real"));
        cmd.env("XDG_CACHE_HOME", &xdg);
        cmd
    } else {
        let portable = manifest
            .portable
            .as_ref()
            .context("helpers require the Portable edition")?;
        if invocation == "chromium" {
            Command::new(root.join(&portable.browser))
        } else {
            let mut cmd = Command::new(root.join(&portable.python));
            if invocation == "trafilatura" {
                cmd.args([
                    "-c",
                    "import sys; from trafilatura.cli import main; sys.argv[0] = 'trafilatura'; sys.exit(main())",
                ]);
            }
            cmd
        }
    };
    cmd.args(&args);
    let mut paths = vec![root.join("launcher-bin")];
    paths.extend(env::split_paths(&env::var_os("PATH").unwrap_or_default()));
    cmd.env("PATH", env::join_paths(paths)?);
    if manifest.portable.is_some() {
        if env::var_os("PUPPETEER_EXECUTABLE_PATH")
            .filter(|value| !value.is_empty())
            .is_none()
        {
            cmd.env(
                "PUPPETEER_EXECUTABLE_PATH",
                root.join("launcher-bin/chromium"),
            );
        }
        if invocation != "chromium"
            && env::var_os("PYTHONPYCACHEPREFIX")
                .filter(|value| !value.is_empty())
                .is_none()
        {
            let pycache = xdg.join("python");
            fs::create_dir_all(&pycache)?;
            cmd.env("PYTHONPYCACHEPREFIX", pycache);
        }
    }
    #[cfg(target_os = "linux")]
    if invocation == "chromium" {
        let mut libraries = vec![root.join("browser/lib")];
        if let Some(existing) = env::var_os("LD_LIBRARY_PATH").filter(|value| !value.is_empty()) {
            libraries.extend(env::split_paths(&existing));
        }
        cmd.env("LD_LIBRARY_PATH", env::join_paths(libraries)?);
        cmd.env("FONTCONFIG_FILE", root.join("browser/fonts.conf"));
        cmd.env("FONTCONFIG_PATH", root.join("browser"));
    }
    let e = cmd.exec();
    bail!("cannot execute bundled {invocation}: {e}")
}
fn main() {
    if let Err(e) = run() {
        eprintln!("omp-portable: {e:#}");
        std::process::exit(1)
    }
}
