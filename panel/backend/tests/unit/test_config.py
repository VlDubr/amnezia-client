from pathlib import PurePosixPath

from app.config import default_scripts_dir


def test_scripts_dir_in_repository_checkout():
    module = PurePosixPath("/src/amnezia-client/panel/backend/app/config.py")
    assert default_scripts_dir(module) == PurePosixPath("/src/amnezia-client/client/server_scripts")


def test_scripts_dir_in_container_image():
    assert default_scripts_dir(PurePosixPath("/app/app/config.py")) == PurePosixPath("/app/server_scripts")
