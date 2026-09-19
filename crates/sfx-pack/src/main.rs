use anyhow::{ensure, Context, Result};
use sfx_format::{digest, hash, validate_manifest, Footer, Manifest};
use std::{
    fs::{self, File},
    io::{self, Seek, SeekFrom, Write},
    os::unix::fs::PermissionsExt,
    path::Path,
};
fn run() -> Result<()> {
    let a: Vec<_> = std::env::args_os().collect();
    ensure!(a.len() == 5, "usage: sfx-pack STUB STAGE OUTPUT MAX_BYTES");
    let stage = Path::new(&a[2]);
    let out = Path::new(&a[3]);
    ensure!(!out.exists(), "output already exists");
    let limit: u64 = a[4].to_str().context("invalid limit")?.parse()?;
    let m: Manifest = serde_json::from_reader(File::open(stage.join("manifest.json"))?)?;
    validate_manifest(&m)?;
    for (p, r) in &m.files {
        let meta = fs::symlink_metadata(stage.join(p))?;
        ensure!(
            meta.is_file() && meta.len() == r.size,
            "invalid component {p}"
        );
        ensure!(
            digest(File::open(stage.join(p))?)? == r.sha256,
            "component digest mismatch: {p}"
        );
    }
    let tmp = out.with_file_name(format!(
        "{}.partial.{}",
        out.file_name()
            .context("missing output filename")?
            .to_string_lossy(),
        std::process::id()
    ));
    let mut f = fs::OpenOptions::new()
        .create_new(true)
        .read(true)
        .write(true)
        .open(&tmp)?;
    let result = (|| -> Result<()> {
        io::copy(&mut File::open(&a[1])?, &mut f)?;
        let offset = f.stream_position()?;
        {
            let enc = zstd::stream::write::Encoder::new(&mut f, 9)?;
            let mut ar = tar::Builder::new(enc);
            for p in std::iter::once("manifest.json").chain(m.files.keys().map(String::as_str)) {
                let mut input = File::open(stage.join(p))?;
                let mut h = tar::Header::new_gnu();
                h.set_size(input.metadata()?.len());
                h.set_mode(if m.files.get(p).map(|r| r.executable).unwrap_or(false) {
                    0o755
                } else {
                    0o644
                });
                h.set_uid(0);
                h.set_gid(0);
                h.set_mtime(0);
                h.set_cksum();
                ar.append_data(&mut h, p, &mut input)?;
            }
            ar.into_inner()?.finish()?;
        }
        let end = f.stream_position()?;
        let length = end - offset;
        f.seek(SeekFrom::Start(offset))?;
        let sum = hash(std::io::Read::take(&mut f, length))?;
        f.seek(SeekFrom::Start(end))?;
        f.write_all(
            &Footer {
                offset,
                length,
                digest: sum,
            }
            .encode(),
        )?;
        ensure!(
            f.stream_position()? < limit,
            "finished artifact exceeds size budget"
        );
        f.set_permissions(fs::Permissions::from_mode(0o755))?;
        f.sync_all()?;
        Ok(())
    })();
    if let Err(e) = result {
        drop(f);
        let _ = fs::remove_file(tmp);
        return Err(e);
    }
    fs::rename(tmp, out)?;
    println!("{}  {}", digest(File::open(out)?)?, out.display());
    Ok(())
}
fn main() {
    if let Err(e) = run() {
        eprintln!("sfx-pack: {e:#}");
        std::process::exit(1)
    }
}
