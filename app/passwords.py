"""密碼雜湊。

原本住在 app/api/projects.py（房間密碼用），L3 把帳號密碼登入加進來之後有了
第二個呼叫端，所以搬成獨立模組 —— 與 app/room_token.py 同一層級，不是新的
抽象層（守則 §1 規則 6）。函式本身一個字沒改。

用 stdlib 的 scrypt，不引入 passlib／bcrypt：多一個相依套件就多一個「在我機器
上可以」的來源，而 scrypt 在 hashlib 裡本來就有。

**兩種密碼共用這裡，但它們不是同一件事：**

- 房間密碼是**共享密碼**，發起人口頭傳給組員（§6.2，不做成員制）
- 帳號密碼保護的是**一個身分**

共用雜湊函式沒有問題；共用心智模型會出事 —— 例如把「房間密碼沒有重設端點」
的理由套到帳號密碼上。
"""

import base64
import hashlib
import hmac
import os


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        algo, salt_b64, digest_b64 = stored.split("$")
        if algo != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
    except (ValueError, TypeError):
        return False
    actual = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return hmac.compare_digest(actual, expected)
