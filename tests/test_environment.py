import os

from tallyguard.environment import load_local_environment


def test_local_environment_loads_ignored_dotenv_without_overriding_process_values(
    tmp_path, monkeypatch
):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "TALLYGUARD_TEST_FROM_FILE=file-value\n"
        "TALLYGUARD_TEST_EXPLICIT=file-must-not-win\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("TALLYGUARD_TEST_FROM_FILE", raising=False)
    monkeypatch.setenv("TALLYGUARD_TEST_EXPLICIT", "process-value")

    assert load_local_environment(env_file) is True
    assert os.environ["TALLYGUARD_TEST_FROM_FILE"] == "file-value"
    assert os.environ["TALLYGUARD_TEST_EXPLICIT"] == "process-value"
