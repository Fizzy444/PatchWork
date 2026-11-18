"""
Docker Ephemeral Sandbox Manager.
Spins up disposable, resource-constrained, isolated containers to run tests.
"""

import os
import shutil
import tempfile
import logging
from typing import Tuple, Optional
from pydantic import BaseModel

from patchwork.config import settings

logger = logging.getLogger("patchwork.sandbox.docker")


class ContainerExecutionResult(BaseModel):
    success: bool
    exit_code: int
    output: str
    timed_out: bool = False
    error: Optional[str] = None


class DockerSandboxManager:
    def __init__(self):
        self._client = None
        self._docker_available: Optional[bool] = None

    def is_available(self) -> bool:
        """Checks if Docker engine is reachable."""
        if self._docker_available is not None:
            return self._docker_available
        try:
            import docker
            client = docker.from_env()
            client.ping()
            self._client = client
            self._docker_available = True
            logger.info("Docker daemon is active and connected.")
            return True
        except Exception as e:
            self._docker_available = False
            logger.warning(f"Docker daemon not reachable ({e}). Will use local runner fallback.")
            return False

    def run_in_container(
        self,
        project_dir: str,
        test_command: str = "pytest -v",
        image: str = "python:3.11-slim",
        timeout_seconds: int = settings.SANDBOX_TIMEOUT_SECONDS
    ) -> ContainerExecutionResult:
        """
        Runs tests inside a disposable container with strict CPU, RAM, and network boundaries.
        Always destroys container after execution.
        """
        if not self.is_available():
            raise RuntimeError("Docker daemon is not available.")

        import docker
        from docker.errors import ContainerError, APIError

        container = None
        try:
            # Prepare volume mount (mount project dir to /workspace inside container)
            abs_project_dir = os.path.abspath(project_dir)
            volumes = {
                abs_project_dir: {"bind": "/workspace", "mode": "rw"}
            }

            # Security and resource limits
            mem_limit = settings.SANDBOX_MEMORY_LIMIT
            cpu_quota = int(settings.SANDBOX_CPU_LIMIT * 100000)  # CFS quota
            cpu_period = 100000

            logger.info(f"Spinning up ephemeral container ({image}) for project: {abs_project_dir}")

            # Script to run inside container: cd /workspace && pip install pytest -q && test_command
            cmd = f"sh -c 'cd /workspace && pip install pytest -q > /dev/null 2>&1 && {test_command}'"

            container = self._client.containers.create(
                image=image,
                command=cmd,
                volumes=volumes,
                working_dir="/workspace",
                mem_limit=mem_limit,
                cpu_quota=cpu_quota,
                cpu_period=cpu_period,
                network_disabled=settings.SANDBOX_NETWORK_DISABLED,
                auto_remove=False,
            )

            container.start()

            # Wait with timeout
            try:
                res = container.wait(timeout=timeout_seconds)
                exit_code = res.get("StatusCode", 1)
                logs = container.logs(stdout=True, stderr=True).decode("utf-8", errors="replace")

                return ContainerExecutionResult(
                    success=(exit_code == 0),
                    exit_code=exit_code,
                    output=logs,
                    timed_out=False
                )
            except Exception as wait_err:
                # Timeout triggered
                container.kill()
                return ContainerExecutionResult(
                    success=False,
                    exit_code=-1,
                    output="Test execution exceeded 2-minute safety timeout (Infinite Loop Prevention).",
                    timed_out=True,
                    error=str(wait_err)
                )

        except Exception as e:
            return ContainerExecutionResult(
                success=False,
                exit_code=1,
                output="",
                error=f"Container execution error: {e}"
            )
        finally:
            if container:
                try:
                    container.remove(force=True)
                    logger.info("Disposable container destroyed cleanly.")
                except Exception:
                    pass
