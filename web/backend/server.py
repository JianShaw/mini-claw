"""Web 服务启动入口：uvicorn 启动 FastAPI 应用。"""

from __future__ import annotations

from dotenv import load_dotenv

# 在 import 其他模块前加载 .env，确保 DEEPSEEK_API_KEY 等环境变量可用
load_dotenv()

import uvicorn


def main():
    uvicorn.run(
        "web.backend.app:create_app",
        factory=True,
        host="0.0.0.0",
        port=8000,
        reload=True,
    )


if __name__ == "__main__":
    main()
