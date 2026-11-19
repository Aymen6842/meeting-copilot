from importlib.resources import files

import worker


def test_package_is_importable_and_ships_type_information() -> None:
    assert files(worker).joinpath("py.typed").is_file()
