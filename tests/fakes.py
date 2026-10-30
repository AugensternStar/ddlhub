"""共享测试替身：不触碰宿主钥匙串、网络与桌面。"""
from __future__ import annotations


class FakeCredentialStore:
    """内存凭据存储（满足 CredentialStore 协议）。

    测试必须与宿主 keyring 解耦：CI（ubuntu-latest）没有可用后端，
    真实 KeyringStore 的任何操作都会抛 NoKeyringError，导致删除账号在
    「先删凭据」这一步提前中止，后续清库断言全部失效。
    """

    def __init__(self) -> None:
        self.data: dict[tuple[str, str], tuple[str, str]] = {}

    def store(self, adapter_key: str, account_uuid: str,
              username: str, password: str) -> None:
        self.data[(adapter_key, account_uuid)] = (username, password)

    def load(self, adapter_key: str, account_uuid: str) -> tuple[str, str] | None:
        return self.data.get((adapter_key, account_uuid))

    def delete(self, adapter_key: str, account_uuid: str) -> None:
        self.data.pop((adapter_key, account_uuid), None)
