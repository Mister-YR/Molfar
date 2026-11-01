import logging
import os
import sys

import uvicorn


def reset_password():
    """Видаляє пароль, заданий з веб-інтерфейсу: знову діє MOLFAR_PASSWORD з /etc/molfar.env."""
    from .db import DB
    DB().set_state("auth_hash", None)
    print("Molfar: web password reset to the one in /etc/molfar.env (default: Molfar). "
          "Restart the service: systemctl restart molfar")


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "reset-password":
        return reset_password()
    logging.basicConfig(level=os.environ.get("MOLFAR_LOG", "INFO"),
                        format="%(levelname)s %(name)s: %(message)s")
    uvicorn.run("molfar.app:app",
                host=os.environ.get("MOLFAR_HOST", "0.0.0.0"),
                port=int(os.environ.get("MOLFAR_PORT", "8080")),
                log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
