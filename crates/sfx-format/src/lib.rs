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
        let size = r.seek(SeekFrom::End(0))?;
        ensure!(size >= FOOTER_SIZE, "truncated footer");
        r.seek(SeekFrom::End(-(FOOTER_SIZE as i64)))?;
        let mut b = [0; 64];
        r.read_exact(&mut b)?;
        ensure!(&b[..8] == MAGIC, "invalid SFX magic");
        ensure!(
            b[8..12] == 1u32.to_le_bytes() && b[12..16] == 1u32.to_le_bytes(),
            "unsupported format/manifest version"
        );
        let offset = u64::from_le_bytes(b[16..24].try_into()?);
        let length = u64::from_le_bytes(b[24..32].try_into()?);
        ensure!(
            offset > 0 && length > 0 && offset.checked_add(length) == Some(size - FOOTER_SIZE),
            "invalid payload bounds"
        );
        Ok(Self {
            offset,
            length,
            digest: b[32..64].try_into()?,
        })
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
pub struct Manifest {
    pub schema: u32,
    pub edition: String,
    pub target: String,
    pub upstream: serde_json::Value,
    pub builder: serde_json::Value,
    pub files: BTreeMap<String, FileRecord>,
}
pub fn validate_manifest(m: &Manifest) -> Result<()> {
    ensure!(
        m.schema == 1 && m.edition == "lite" && m.target == "linux-x64",
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
}
