"""服务端拥有的只读 worker；隔离启动，不加载 cwd 或用户 Python 模块。"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# -I 不包含脚本目录；只加入经过部署信任的后端目录，永不加入工作区根。
if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

WORKER_ERRORS = frozenset({
    'WORKSPACE_ROOT_NOT_AVAILABLE', 'WORKSPACE_PATH_INVALID', 'WORKSPACE_PATH_OUTSIDE_ROOT',
    'WORKSPACE_PATH_SENSITIVE', 'WORKSPACE_FILE_NOT_FOUND', 'WORKSPACE_FILE_NOT_TEXT',
    'WORKSPACE_FILE_TOO_LARGE', 'WORKSPACE_DIRECTORY_NOT_FOUND', 'COMMAND_ARGUMENT_INVALID',
    'COMMAND_NOT_ALLOWED', 'COMMAND_FAILED', 'WORKSPACE_FILE_REVISION_CONFLICT',
})


async def main() -> int:
    """读取有界控制输入，重复路径校验并向 stdout 写模型专用结果。"""
    from app.workspaces.commands import WorkspaceCommandError, validate_command
    from app.workspaces.files import WorkspaceFileError, WorkspaceFileService
    from app.workspaces.paths import WorkspacePathError, resolve_workspace_root
    try:
        payload = sys.stdin.buffer.read(32769)
        if len(payload) > 32768:
            raise WorkspaceCommandError('COMMAND_ARGUMENT_INVALID')
        request = json.loads(payload)
        root = resolve_workspace_root(request['root'])
        if root != Path.cwd() or str(root) != request['root']:
            raise WorkspaceCommandError('WORKSPACE_ROOT_NOT_AVAILABLE')
        command = request['command']
        args = validate_command(root, command, request['args'])
        service = WorkspaceFileService(root=root, execution_id='command-worker')
        if command == 'pwd':
            output = str(root) + '\n'
        elif command == 'list':
            output = service.json_result(await service.list(args['path']))
        else:
            # 原生读取先校验全文件 UTF-8/大小；循环页读取避免提升 W1a 公开 max_bytes 上限。
            chunks = []
            offset = 0
            expected_hash = None
            while True:
                part = await service.read(args['path'], offset_bytes=offset)
                if expected_hash is not None and part.sha256 != expected_hash:
                    raise WorkspaceCommandError('WORKSPACE_FILE_REVISION_CONFLICT')
                expected_hash = part.sha256
                chunks.append(part.text)
                if part.eof:
                    break
                offset = part.next_offset
            content = ''.join(chunks)
            output = content if command == 'read' else json.dumps({
                'bytes': len(content.encode('utf-8')), 'lines': len(content.splitlines()),
            })
        sys.stdout.buffer.write(output.encode('utf-8'))
        return 0
    except (WorkspaceCommandError, WorkspaceFileError, WorkspacePathError) as exc:
        sys.stderr.write(exc.code if exc.code in WORKER_ERRORS else 'COMMAND_FAILED')
    except Exception:
        # 异常绝不能把 cwd、文件正文或环境回显到宿主诊断产物。
        sys.stderr.write('COMMAND_FAILED')
    return 1


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
