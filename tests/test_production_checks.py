from psycopg.conninfo import conninfo_to_dict

from scripts.production_checks import client_environment


def test_database_client_arguments_do_not_contain_password():
    arguments, environment = client_environment(
        "host=localhost dbname=disposable user=operator password=not-for-command-lines"
    )
    assert "not-for-command-lines" not in arguments
    assert "password" not in conninfo_to_dict(arguments)
    assert environment["PGPASSWORD"] == "not-for-command-lines"
    assert conninfo_to_dict(arguments)["dbname"] == "disposable"
