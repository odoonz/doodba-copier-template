import time
import uuid
from itertools import product
from pathlib import Path

import requests
from copier import copy
from plumbum import local
from plumbum.cmd import docker_compose


def test_multiple_domains_prod(
    cloned_template: Path,
    supported_odoo_version: float,
    tmp_path: Path,
    traefik_host: str,
):
    """Test multiple domains are produced properly."""
    data = {
        "odoo_version": supported_odoo_version,
        "project_name": uuid.uuid4().hex,
        "domains_prod": [
            # main0 has no TLS
            {"hosts": [f"main0.{traefik_host}.sslip.io"], "cert_resolver": None},
            {
                "hosts": [
                    f"alt0.main0.{traefik_host}.sslip.io",
                    f"alt1.main0.{traefik_host}.sslip.io",
                ],
                "cert_resolver": None,
            },
            # main1 has self-signed certificates
            {"hosts": [f"main1.{traefik_host}.sslip.io"], "cert_resolver": True},
            {
                "hosts": [
                    f"alt0.main1.{traefik_host}.sslip.io",
                    f"alt1.main1.{traefik_host}.sslip.io",
                ],
                "cert_resolver": True,
            },
        ],
    }
    dc = docker_compose["-f", "prod.yaml"]
    with local.cwd(tmp_path):
        copy(
            src_path=str(cloned_template),
            dst_path=".",
            vcs_ref="test",
            force=True,
            data=data,
        )
        try:
            dc("build")
            dc(
                "run", "--rm", "odoo", "--stop-after-init", "-i", "base",
            )
            dc("up", "-d")
            time.sleep(10)
            for main_num, alt_num in product(range(2), range(2)):
                http = "http" if main_num == 0 else "https"
                main_domain = f"main{main_num}.{traefik_host}.sslip.io"
                alt_domain = f"alt{alt_num}.{main_domain}"
                response = requests.get(f"http://{alt_domain}/web/login", verify=False)
                assert response.ok
                assert response.url == f"{http}://{main_domain}/web/login"
            bad_response = requests.get(f"http://missing.{traefik_host}.sslip.io")
            assert bad_response.status_code == 404
            bad_response = requests.get(
                f"https://missing.{traefik_host}.sslip.io", verify=False
            )
            assert bad_response.status_code == 404
        finally:
            dc("down", "--volumes", "--remove-orphans")


def test_multiple_domains_test(
    cloned_template: Path,
    supported_odoo_version: float,
    tmp_path: Path,
    traefik_host: str,
):
    """Test multiple domains are produced properly."""
    data = {
        "traefik_cert_resolver": None,
        "odoo_version": supported_odoo_version,
        "project_name": uuid.uuid4().hex,
        "domains_staging": [
            f"demo0.{traefik_host}.sslip.io",
            f"demo1.{traefik_host}.sslip.io",
        ],
    }
    dc = docker_compose["-f", "test.yaml"]
    with local.cwd(tmp_path):
        copy(
            src_path=str(cloned_template),
            dst_path=".",
            vcs_ref="test",
            force=True,
            data=data,
        )
        try:
            dc("build")
            dc(
                "run", "--rm", "odoo", "--stop-after-init", "-i", "base",
            )
            dc("up", "-d")
            time.sleep(10)
            for staging_domain in data["domains_staging"]:
                response = requests.get(f"http://{staging_domain}/web/login")
                assert response.ok
                assert response.url == f"http://{staging_domain}/web/login"
            bad_response = requests.get(f"http://missing.{traefik_host}.sslip.io")
            assert bad_response.status_code == 404
        finally:
            dc("down", "--volumes", "--remove-orphans")
