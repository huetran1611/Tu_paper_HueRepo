"""Publish irace checkpoints atomically; keep three immutable recovery copies."""
import os
import shutil
import sys
import time
from pathlib import Path


def publish(source: Path, nominal: Path) -> None:
    checkpoints=nominal.parent / "checkpoints"
    checkpoints.mkdir(exist_ok=True)
    with source.open("rb+") as stream:
        os.fsync(stream.fileno())
    version=checkpoints / f"irace-{time.time_ns()}.Rdata"
    os.replace(source,version)
    temporary=nominal.with_name(nominal.name+".publish.tmp")
    shutil.copyfile(version,temporary)
    with temporary.open("rb+") as stream:
        os.fsync(stream.fileno())
    os.replace(temporary,nominal)
    for old in sorted(checkpoints.glob("irace-*.Rdata"))[:-3]:
        old.unlink()


if __name__=="__main__":
    publish(Path(sys.argv[1]),Path(sys.argv[2]))
