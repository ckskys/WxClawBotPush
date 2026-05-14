import sys
from database import init_db
from auth import create_admin


def main():
    init_db()
    if len(sys.argv) != 3:
        print("用法: python -m wxclawbotpush.create_admin <用户名> <密码>")
        sys.exit(1)

    username, password = sys.argv[1], sys.argv[2]
    user_id = create_admin(username, password)
    if user_id:
        print(f"管理员已创建: user_id={user_id}")
    else:
        print("管理员创建失败（可能已存在）")


if __name__ == "__main__":
    main()
