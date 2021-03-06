import json
import os
from pathlib import Path

import pytest
import yaml
from packaging import version
from plumbum import FG, local
from plumbum.cmd import git
from plumbum.machines.local import LocalCommand

with open("copier.yml") as copier_fd:
    COPIER_SETTINGS = yaml.safe_load(copier_fd)

# Different tests test different Odoo versions
OLDEST_SUPPORTED_ODOO_VERSION = 8.0
ALL_ODOO_VERSIONS = tuple(COPIER_SETTINGS["odoo_version"]["choices"])
SUPPORTED_ODOO_VERSIONS = tuple(
    v for v in ALL_ODOO_VERSIONS if v >= OLDEST_SUPPORTED_ODOO_VERSION
)
LAST_ODOO_VERSION = max(SUPPORTED_ODOO_VERSIONS)
SELECTED_ODOO_VERSIONS = (
    frozenset(map(float, os.environ.get("SELECTED_ODOO_VERSIONS", "").split()))
    or ALL_ODOO_VERSIONS
)

# Traefik versions matrix
ALL_TRAEFIK_VERSIONS = ("latest", "2.2", "1.7")


@pytest.fixture(params=ALL_ODOO_VERSIONS)
def any_odoo_version(request) -> float:
    """Returns any usable odoo version."""
    if request.param not in SELECTED_ODOO_VERSIONS:
        pytest.skip("odoo version not in selected range")
    return request.param


@pytest.fixture(params=SUPPORTED_ODOO_VERSIONS)
def supported_odoo_version(request) -> float:
    """Returns any usable odoo version."""
    if request.param not in SELECTED_ODOO_VERSIONS:
        pytest.skip("supported odoo version not in selected range")
    return request.param


@pytest.fixture()
def cloned_template(tmp_path_factory):
    """This repo cloned to a temporary destination.

    The clone will include dirty changes, and it will have a 'test' tag in its HEAD.

    It returns the local `Path` to the clone.
    """
    patches = [git("diff", "--cached"), git("diff")]
    with tmp_path_factory.mktemp("cloned_template_") as dirty_template_clone:
        git("clone", ".", dirty_template_clone)
        with local.cwd(dirty_template_clone):
            git("config", "commit.gpgsign", "false")
            for patch in patches:
                if patch:
                    (git["apply", "--reject"] << patch)()
                    git("add", ".")
                    git(
                        "commit",
                        "--author=Test<test@test>",
                        "--message=dirty changes",
                        "--no-verify",
                    )
            git("tag", "--force", "test")
        yield dirty_template_clone


@pytest.fixture()
def docker() -> LocalCommand:
    if os.environ.get("DOCKER_TEST") != "1":
        pytest.skip("Missing DOCKER_TEST=1 env variable")
    try:
        from plumbum.cmd import docker
    except ImportError:
        pytest.skip("Need docker CLI to run this test")
    docker["info"] & FG
    return docker


@pytest.fixture()
def versionless_odoo_autoskip(request):
    """Fixture to automatically skip tests when testing for older odoo versions."""
    is_version_specific_test = (
        "any_odoo_version" in request.fixturenames
        or "supported_odoo_version" in request.fixturenames
    )
    if LAST_ODOO_VERSION not in SELECTED_ODOO_VERSIONS and not is_version_specific_test:
        pytest.skip("version-independent test in old versioned odoo test session")


@pytest.fixture(params=ALL_TRAEFIK_VERSIONS)
def traefik_host(docker: LocalCommand, request):
    """Fixture to indicate where to find a running traefik instance."""
    traefik_run = docker[
        "container",
        "run",
        "--detach",
        "--privileged",
        "--network=inverseproxy_shared",
        "--volume=/var/run/docker.sock:/var/run/docker.sock:ro",
        f"traefik:{request.param}",
    ]
    try:
        if request.param == "latest" or version.parse(request.param) >= version.parse(
            "2"
        ):
            traefik_container = traefik_run(
                "--entrypoints.web-main.address=:80",
                "--log.level=debug",
                "--providers.docker.exposedByDefault=false",
                "--providers.docker.network=inverseproxy_shared",
                "--providers.docker=true",
            ).strip()
        else:
            traefik_container = traefik_run(
                "--logLevel=debug",
                "--defaultEntryPoints=http",
                "--docker.exposedByDefault=false",
                "--docker.watch",
                "--docker",
                "--entryPoints=Name:http Address::80 Compress:on",
            ).strip()
        traefik_details = json.loads(docker("container", "inspect", traefik_container))
        assert (
            len(traefik_details) == 1
        ), "Impossible... did you trigger a race condition?"
        yield traefik_details[0]["NetworkSettings"]["Networks"]["inverseproxy_shared"][
            "IPAddress"
        ]
    finally:
        docker("container", "rm", "--force", traefik_container)


def teardown_function(function):
    pre_commit_log = (
        Path("~") / ".cache" / "pre-commit" / "pre-commit.log"
    ).expanduser()
    if pre_commit_log.is_file():
        print(pre_commit_log.read_text())
        pre_commit_log.unlink()
