from importlib.resources import files

import contracts


def test_package_is_importable_and_ships_type_information() -> None:
    assert files(contracts).joinpath("py.typed").is_file()
