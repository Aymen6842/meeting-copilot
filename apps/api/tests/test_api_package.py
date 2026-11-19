from importlib.resources import files

import app


def test_package_is_importable_and_ships_type_information() -> None:
    assert files(app).joinpath("py.typed").is_file()
