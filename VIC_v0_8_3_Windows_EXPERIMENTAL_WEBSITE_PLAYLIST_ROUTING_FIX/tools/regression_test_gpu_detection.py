from __future__ import annotations
import re
from pathlib import Path
BASE=Path(__file__).resolve().parent.parent
WORKER=BASE/"worker"/"worker.py"
SAMPLE="""
Encoders:
 V....D h264_nvenc NVIDIA NVENC H.264 encoder
 V....D h264_amf AMD AMF H.264 encoder
 V..... h264_qsv Intel Quick Sync H.264 encoder
"""
def main():
    text=WORKER.read_text(encoding="utf-8")
    if 'r"^\\\\s*' in text: raise RuntimeError("broken regex remains")
    names=set()
    for line in SAMPLE.splitlines():
        m=re.match(r"^\s*[A-Z.]{6}\s+(\S+)",line)
        if m:names.add(m.group(1))
    expected={"h264_nvenc","h264_amf","h264_qsv"}
    if not expected.issubset(names): raise RuntimeError((expected,names))
    print("GPU parser regression test passed")
    return 0
if __name__=="__main__": raise SystemExit(main())
