"""正常 World 包装器加受控类型，仍验证真实子进程切换和独立存档。"""
import os
import sys
from pathlib import Path

backend = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(backend))
os.environ['PYTHONPATH'] = os.pathsep.join([str(backend / 'tests'), str(backend)])
from world_types_fixture import install
install()
from scripts.run_world_server import run

if __name__ == '__main__':
    raise SystemExit(run(application='world_types_e2e_app:app'))
