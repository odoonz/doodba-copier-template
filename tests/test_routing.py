import time
import uuid
from pathlib import Path

import pytest
import requests
from copier import copy
from plumbum import local
from plumbum.cmd import docker_compose


@pytest.mark.parametrize("environment", ("test", "prod"))
def test_multiple_domains(
    cloned_template: Path,
    supported_odoo_version: float,
    tmp_path: Path,
    traefik_host: str,
    environment: str,
):
    """Test multiple domains are produced properly."""
    base_domain = f"{traefik_host}.sslip.io"
    base_path = f"{base_domain}/web/login"
    data = {
        "odoo_version": supported_odoo_version,
        "project_name": uuid.uuid4().hex,
        f"domains_{environment}": [
            # main0 has no TLS
            {"hosts": [f"main0.{base_domain}"], "cert_resolver": False},
            {
                "hosts": [f"alt0.main0.{base_domain}", f"alt1.main0.{base_domain}"],
                "cert_resolver": None,
                "redirect_to": f"main0.{base_domain}",
            },
            # main1 has self-signed certificates
            {"hosts": [f"main1.{base_domain}"], "cert_resolver": True},
            {
                "hosts": [f"alt0.main1.{base_domain}", f"alt1.main1.{base_domain}"],
                "cert_resolver": True,
                "redirect_to": f"main1.{base_domain}",
            },
        ],
    }
    dc = docker_compose["-f", f"{environment}.yaml"]
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
            # main0, no TLS
            response = requests.get(f"http://main0.{base_path}")
            assert response.ok
            assert response.url == f"http://main0.{base_path}"
            # alt0 and alt1, no TLS
            for alt_num in range(2):
                response = requests.get(f"http://alt{alt_num}.main0.{base_path}")
                assert response.ok
                assert response.url == f"http://main0.{base_path}"
            # main1, with self-signed TLS
            response = requests.get(f"http://main1.{base_path}", verify=False)
            assert response.ok
            assert response.url == f"https://main1.{base_path}"
            # alt0 and alt1, with self-signed TLS
            for alt_num in range(2):
                response = requests.get(
                    f"http://alt{alt_num}.main1.{base_path}", verify=False,
                )
                assert response.ok
                assert response.url == f"https://main1.{base_path}"
            # missing, which fails with 404, both with and without TLS
            bad_response = requests.get(f"http://missing.{base_path}")
            assert bad_response.status_code == 404
            bad_response = requests.get(f"https://missing.{base_path}", verify=False)
            assert bad_response.status_code == 404
        finally:
            dc("down", "--volumes", "--remove-orphans")
