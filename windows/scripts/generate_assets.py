from __future__ import annotations
from pathlib import Path
import struct
import zlib

def png(size: int) -> bytes:
    background, paper, accent = (20,59,89,255), (255,255,255,255), (174,210,230,255)
    raw = bytearray()
    for y in range(size):
        raw.append(0)
        for x in range(size):
            front = size*.22 <= x < size*.76 and size*.15 <= y < size*.84
            back = size*.31 <= x < size*.84 and size*.22 <= y < size*.91
            color = accent if back and not front else paper if front else background
            if front and size*.31 <= x < size*.67 and (size*.32 <= y < size*.37 or size*.44 <= y < size*.49):
                color = accent
            if front and size*.31 <= x < size*.62 and size*.56 <= y < size*.61:
                color = accent
            raw.extend(color)
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I",len(payload))+kind+payload+struct.pack(">I",zlib.crc32(kind+payload)&0xffffffff)
    header = struct.pack(">IIBBBBB",size,size,8,6,0,0,0)
    return b"\x89PNG\r\n\x1a\n"+chunk(b"IHDR",header)+chunk(b"IDAT",zlib.compress(bytes(raw),9))+chunk(b"IEND",b"")

def main() -> None:
    output=Path(__file__).resolve().parents[2]/"artifacts"/"package-assets"
    output.mkdir(parents=True,exist_ok=True)
    for name,size in (("StoreLogo.png",50),("Square44x44Logo.png",44),("Square150x150Logo.png",150)):
        (output/name).write_bytes(png(size))

if __name__=="__main__":
    main()
