use anyhow::{bail, ensure, Result};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    io::{Read, Seek, SeekFrom},
    path::{Component, Path},
};
pub const FOOTER_SIZE: u64 = 64;
pub const MAGIC: &[u8; 8] = b"OMPSFX01";
#[derive(Debug)]
pub struct Footer {
    pub offset: u64,
    pub length: u64,
    pub digest: [u8; 32],
}
impl Footer {
    pub fn encode(&self) -> [u8; 64] {
        let mut b = [0; 64];
        b[..8].copy_from_slice(MAGIC);
        b[8..12].copy_from_slice(&1u32.to_le_bytes());
        b[12..16].copy_from_slice(&1u32.to_le_bytes());
        b[16..24].copy_from_slice(&self.offset.to_le_bytes());
        b[24..32].copy_from_slice(&self.length.to_le_bytes());
        b[32..64].copy_from_slice(&self.digest);
        b
    }
    pub fn read(r: &mut (impl Read + Seek)) -> Result<Self> {
        let file_size = r.seek(SeekFrom::End(0))?;
        let (origin, size) = payload_region(r, file_size)?;
        ensure!(size >= FOOTER_SIZE, "truncated footer");
        r.seek(SeekFrom::Start(origin + size - FOOTER_SIZE))?;
        let mut b = [0; 64];
        r.read_exact(&mut b)?;
        ensure!(&b[..8] == MAGIC, "invalid SFX magic");
        ensure!(
            b[8..12] == 1u32.to_le_bytes() && b[12..16] == 1u32.to_le_bytes(),
            "unsupported format/manifest version"
        );
        let relative = u64::from_le_bytes(b[16..24].try_into()?);
        let length = u64::from_le_bytes(b[24..32].try_into()?);
        ensure!(
            length > 0 && relative.checked_add(length) == Some(size - FOOTER_SIZE),
            "invalid payload bounds"
        );
        let offset = origin
            .checked_add(relative)
            .ok_or_else(|| anyhow::anyhow!("payload offset overflow"))?;
        Ok(Self {
            offset,
            length,
            digest: b[32..64].try_into()?,
        })
    }
}
/// Mach-O stores the compressed stream plus footer in a linker-created section.
/// This leaves macOS signing metadata free to occupy EOF; no marker scanning.
fn payload_region(r: &mut (impl Read + Seek), file_size: u64) -> Result<(u64, u64)> {
    ensure!(file_size >= 4, "truncated executable");
    r.seek(SeekFrom::Start(0))?;
    let mut magic = [0; 4];
    r.read_exact(&mut magic)?;
    if u32::from_le_bytes(magic) != 0xfeedfacf {
        return Ok((0, file_size));
    }
    ensure!(file_size >= 32, "truncated Mach-O header");
    let mut header = [0; 28];
    r.read_exact(&mut header)?;
    let ncmds = u32::from_le_bytes(header[12..16].try_into()?);
    let cmdbytes = u32::from_le_bytes(header[16..20].try_into()?) as u64;
    ensure!(
        cmdbytes <= 1024 * 1024 && 32 + cmdbytes <= file_size && ncmds as u64 <= cmdbytes / 8,
        "invalid Mach-O commands"
    );
    let mut pos = 32u64;
    let mut found = None;
    for _ in 0..ncmds {
        r.seek(SeekFrom::Start(pos))?;
        let mut head = [0; 8];
        r.read_exact(&mut head)?;
        let cmd = u32::from_le_bytes(head[..4].try_into()?);
        let len = u32::from_le_bytes(head[4..].try_into()?) as u64;
        ensure!(
            len >= 8 && pos + len <= 32 + cmdbytes,
            "invalid Mach-O command bounds"
        );
        if cmd == 0x19 {
            ensure!(len >= 72, "truncated Mach-O segment");
            let mut seg = [0; 64];
            r.read_exact(&mut seg)?;
            let sections = u32::from_le_bytes(seg[56..60].try_into()?) as u64;
            ensure!(72 + sections * 80 <= len, "invalid Mach-O section count");
            for _ in 0..sections {
                let mut section = [0; 80];
                r.read_exact(&mut section)?;
                if &section[..16] == b"__payload\0\0\0\0\0\0\0"
                    && &section[16..32] == b"__OMP\0\0\0\0\0\0\0\0\0\0\0"
                {
                    ensure!(found.is_none(), "duplicate Mach-O payload section");
                    let size = u64::from_le_bytes(section[40..48].try_into()?);
                    let offset = u32::from_le_bytes(section[48..52].try_into()?) as u64;
                    ensure!(
                        offset >= 32 + cmdbytes
                            && offset.checked_add(size).is_some_and(|end| end <= file_size),
                        "invalid Mach-O payload bounds"
                    );
                    found = Some((offset, size));
                }
            }
        }
        pos += len;
    }
    found.ok_or_else(|| anyhow::anyhow!("Mach-O executable has no __OMP,__payload section"))
}
pub fn native_target() -> &'static str {
    match (std::env::consts::OS, std::env::consts::ARCH) {
        ("linux", "x86_64") => "linux-x64",
        ("linux", "aarch64") => "linux-arm64",
        ("macos", "x86_64") => "darwin-x64",
        ("macos", "aarch64") => "darwin-arm64",
        _ => "unsupported",
    }
}
pub fn hash(mut r: impl Read) -> Result<[u8; 32]> {
    let mut h = Sha256::new();
    let mut b = [0; 131072];
    loop {
        let n = r.read(&mut b)?;
        if n == 0 {
            break;
        }
        h.update(&b[..n]);
    }
    Ok(h.finalize().into())
}
pub fn digest(r: impl Read) -> Result<String> {
    Ok(hex::encode(hash(r)?))
}
pub fn safe_path(p: &Path) -> Result<()> {
    ensure!(!p.as_os_str().is_empty(), "empty path");
    for c in p.components() {
        if !matches!(c, Component::Normal(_)) {
            bail!("unsafe payload path: {}", p.display())
        }
    }
    Ok(())
}
#[derive(Serialize, Deserialize, Debug)]
#[serde(deny_unknown_fields)]
pub struct FileRecord {
    pub sha256: String,
    pub size: u64,
    pub executable: bool,
}
#[derive(Serialize, Deserialize, Debug)]
#[serde(deny_unknown_fields)]
pub struct Portable {
    pub python: String,
    pub browser: String,
    pub components: serde_json::Value,
}

#[derive(Serialize, Deserialize, Debug)]
#[serde(deny_unknown_fields)]
pub struct Manifest {
    pub schema: u32,
    pub edition: String,
    pub target: String,
    pub upstream: serde_json::Value,
    pub builder: serde_json::Value,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub portable: Option<Portable>,
    pub files: BTreeMap<String, FileRecord>,
}
pub fn validate_manifest(m: &Manifest) -> Result<()> {
    ensure!(
        m.schema == 1
            && ["lite", "portable"].contains(&m.edition.as_str())
            && ["linux-x64", "linux-arm64", "darwin-x64", "darwin-arm64"]
                .contains(&m.target.as_str()),
        "unsupported manifest"
    );
    ensure!(
        m.files.contains_key("bin/omp.real") && m.files.contains_key("launcher-bin/omp"),
        "missing launch files"
    );
    ensure!(
        m.files["bin/omp.real"].executable && m.files["launcher-bin/omp"].executable,
        "launch files must be executable"
    );
    for (p, r) in &m.files {
        safe_path(Path::new(p))?;
        ensure!(p != "manifest.json" && p != "READY", "reserved path");
        ensure!(
            r.sha256.len() == 64 && r.sha256.bytes().all(|c| c.is_ascii_hexdigit()),
            "invalid digest"
        );
    }
    match (&m.edition[..], &m.portable) {
        ("lite", None) => {}
        ("lite", Some(_)) => bail!("Lite manifest must not contain portable metadata"),
        ("portable", None) => bail!("Portable manifest is missing portable metadata"),
        ("portable", Some(portable)) => {
            ensure!(
                portable.python == "helpers/python/bin/python3",
                "invalid bundled Python path"
            );
            safe_path(Path::new(&portable.browser))?;
            ensure!(
                portable.browser.starts_with("browser/"),
                "browser executable must be under browser/"
            );
            for p in [
                portable.python.as_str(),
                portable.browser.as_str(),
                "launcher-bin/python",
                "launcher-bin/python3",
                "launcher-bin/trafilatura",
                "launcher-bin/chromium",
            ] {
                let r = m
                    .files
                    .get(p)
                    .ok_or_else(|| anyhow::anyhow!("missing Portable executable: {p}"))?;
                ensure!(r.executable, "Portable executable is not executable: {p}");
            }
        }
        _ => unreachable!(),
    }
    Ok(())
}
#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;
    #[test]
    fn footer_roundtrip() {
        let f = Footer {
            offset: 10,
            length: 20,
            digest: [7; 32],
        };
        let mut b = vec![0; 30];
        b.extend(f.encode());
        assert_eq!(Footer::read(&mut Cursor::new(b)).unwrap().digest, [7; 32]);
    }
    #[test]
    fn unsafe_paths() {
        for p in ["../x", "/x", "x/../y", ""] {
            assert!(safe_path(Path::new(p)).is_err());
        }
    }
    #[test]
    fn bounds() {
        let f = Footer {
            offset: u64::MAX,
            length: 20,
            digest: [0; 32],
        };
        assert!(Footer::read(&mut Cursor::new(f.encode())).is_err());
    }
    fn macho_fixture() -> Vec<u8> {
        let payload = [0x5a; 17];
        let origin = 256usize;
        let section_size = payload.len() + 64;
        let mut b = vec![0; origin + section_size + 128]; // signature bytes AFTER section
        b[0..4].copy_from_slice(&0xfeedfacfu32.to_le_bytes());
        b[16..20].copy_from_slice(&1u32.to_le_bytes());
        b[20..24].copy_from_slice(&152u32.to_le_bytes());
        b[32..36].copy_from_slice(&0x19u32.to_le_bytes());
        b[36..40].copy_from_slice(&152u32.to_le_bytes());
        b[40..45].copy_from_slice(b"__OMP");
        b[96..100].copy_from_slice(&1u32.to_le_bytes());
        b[104..113].copy_from_slice(b"__payload");
        b[120..125].copy_from_slice(b"__OMP");
        b[144..152].copy_from_slice(&(section_size as u64).to_le_bytes());
        b[152..156].copy_from_slice(&(origin as u32).to_le_bytes());
        b[origin..origin + payload.len()].copy_from_slice(&payload);
        b[origin + payload.len()..origin + section_size].copy_from_slice(
            &Footer {
                offset: 0,
                length: payload.len() as u64,
                digest: [3; 32],
            }
            .encode(),
        );
        b
    }
    #[test]
    fn macho_signed_tail_is_not_payload() {
        let f = Footer::read(&mut Cursor::new(macho_fixture())).unwrap();
        assert_eq!(f.offset, 256);
        assert_eq!(f.length, 17);
        assert_eq!(f.digest, [3; 32]);
    }
    #[test]
    fn macho_bounds_rejected() {
        let mut b = macho_fixture();
        b[144..152].copy_from_slice(&u64::MAX.to_le_bytes());
        assert!(Footer::read(&mut Cursor::new(b)).is_err());
    }
    #[test]
    fn macho_invalid_commands_rejected() {
        let mut b = macho_fixture();
        b[36..40].copy_from_slice(&8u32.to_le_bytes());
        assert!(Footer::read(&mut Cursor::new(b)).is_err());
    }
    #[test]
    fn macho_missing_section_rejected() {
        let mut b = macho_fixture();
        b[104] = b'x';
        assert!(Footer::read(&mut Cursor::new(b)).is_err());
    }
    #[test]
    fn payload_only_footer() {
        let mut b = vec![7; 24];
        b.extend(
            Footer {
                offset: 0,
                length: 24,
                digest: [9; 32],
            }
            .encode(),
        );
        assert_eq!(Footer::read(&mut Cursor::new(b)).unwrap().offset, 0);
    }
}
