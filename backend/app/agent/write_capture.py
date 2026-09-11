"""执行作用域内的私有写入凭据，不经过模型结果或框架事件 payload。"""
import asyncio
from contextvars import ContextVar

from .tool_capture import bounded_text
from .tool_context import tool_call_id


class WriteReceipt:
    """只持有当前调用的有界采集与计算 ticket；不持久化完整快照。"""

    def __init__(self, path: str):
        """Args:
            path：本次模型请求的路径，只在授权提交后规范化并展示。
        """
        # 不能把截断后的路径冒充真实目标；超长身份只能降级，原调用参数仍由文件层验证。
        self.path = path if len(path) <= 4096 else None
        self.ticket = None
        self.output = None
        self.value = {'version': 1, 'availability': 'result_unconfirmed', 'reason': None, 'files': []}

    def applied(self, before: bytes | None, after: bytes) -> None:
        """文件服务确认提交后同步预留计算容量，不能抛错改变写入结果。

        Args:
            before：受锁保护的旧版本；新建为 None。
            after：成功提交的新版本。
        """
        from ..workspaces.diffs import change_metadata, current_pool
        from ..workspaces.paths import normalize_relative_path
        try:
            if self.path is None:
                raise ValueError('Capture path exceeds limit')
            value = change_metadata(normalize_relative_path(self.path), before, after)
            self.value = value
            self.ticket = current_pool().submit(before, after, value)
        except Exception:
            self.value.update(availability='unavailable', reason='capture_failed')

    async def finish(self, model_output: str) -> None:
        """仅在写锁释放后等待计算，原始工具结果与 diff 分开保存。

        Args:
            model_output：本次已确认的 write 返回值，不包含 diff。
        """
        self.output = bounded_text(model_output)
        if self.ticket is not None:
            try:
                self.value = await self.ticket.result()
            except asyncio.CancelledError:
                if self.ticket.value['availability'] != 'unavailable':
                    self.ticket.unavailable('cancelled')
                raise
            except Exception:
                # 采集基础设施失败不将已成功写入伪装为失败，也不诱发模型重写。
                self.ticket.unavailable('capture_failed')
            finally:
                self.value = self.ticket.value

    def not_executed(self) -> None:
        """只用于文件服务已知的写前拒绝，不能用于 OS 部分写入异常。"""
        self.value = {'version': 1, 'availability': 'not_executed', 'reason': None, 'files': []}

    def release(self) -> None:
        """释放尚未开始等待或已收尾的 ticket，不抹掉有界结果。"""
        if self.ticket is not None:
            self.ticket.release()


class WriteCaptureScope:
    """一个 generation 的采集所有者；框架子任务只继承同一受控对象。"""

    def __init__(self):
        """创建仅由当前 generation 持有的空凭据集合。"""
        self.receipts: dict[str, WriteReceipt] = {}

    def begin(self, call_id: str, path: str) -> WriteReceipt | None:
        """限定未消费的凭据数量，未知/重复身份不创建第二份采集。

        Args:
            call_id：GuardedTool 绑定的宿主调用身份。
            path：调用路径，不作为全局缓存键。
        """
        if call_id in self.receipts or len(self.receipts) >= 32:
            return None
        receipt = WriteReceipt(path)
        self.receipts[call_id] = receipt
        return receipt

    def take(self, call_id: str, output: dict | None = None) -> dict | None:
        """消息所有者消费一次凭据；不把私有数据返回模型。

        Args:
            call_id：匹配的原始工具调用。
            output：防腐层观察到的普通工具输出，取消时可以为空。
        """
        receipt = self.receipts.pop(call_id, None)
        if receipt is None:
            return output
        receipt.release()
        return {'format': 'write-v1', 'result': output or receipt.output, 'write': receipt.value}

    def clear(self) -> None:
        """终止执行时释放未消费的有界凭据。"""
        for receipt in self.receipts.values():
            receipt.release()
        self.receipts.clear()


write_capture_scope: ContextVar[WriteCaptureScope | None] = ContextVar('roleplex_write_capture_scope', default=None)


def begin_write_capture(path: str) -> WriteReceipt | None:
    """只有在消息所有者已建立作用域且有真实调用身份时采集。

    Args:
        path：本次 write 的路径。
    """
    scope, call_id = write_capture_scope.get(), tool_call_id.get()
    return scope.begin(call_id, path) if scope is not None and call_id else None


def take_write_capture(call_id: str, output: dict | None) -> dict | None:
    """供防腐层提取私有输出，不接收模型传入的采集对象。

    Args:
        call_id：宿主调用身份。
        output：显式白名单提取的普通结果。
    """
    scope = write_capture_scope.get()
    return scope.take(call_id, output) if scope else output
