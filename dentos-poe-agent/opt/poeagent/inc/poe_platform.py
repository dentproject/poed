'''
Copyright 2021 Delta Electronic Inc.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
'''


import importlib.util
from abc import abstractmethod

from agent_constants import AgentConstants
from drivers.pd69200.poe_driver import PoeDriver_microsemi_pd69200
from poe_log import PoeLog
from poe_common import print_stderr
import importlib.util
import os

class PoePlatform(PoeDriver_microsemi_pd69200):
    @abstractmethod
    def port_count(self) -> int:
        pass

    @property
    @abstractmethod
    def default_power_limits(self) -> dict[int, int]:
        pass

    @abstractmethod
    def init_poe(self, skip_port_init: bool) -> dict:
        pass

    @abstractmethod
    def power_bank_to_str(self, bank_index: int) -> str:
        pass


class PoePlatformFactory:
    """Platform HAL factory, facilitating the instantiation of a HAL object."""

    def __init__(self) -> None:
        self._log = PoeLog()

    @staticmethod
    def create_platform_from_bootcmd(bootcmd_path: str) -> tuple[PoePlatform | None, str | None]:
        """Create the platform HAL based on the bootcmd string

        Args:
            bootcmd_path (str): Path to the bootcmd file

        Returns:
            tuple[PoePlatform | None, str | None]: The platform HAL instance,
            if successful
        """
        factory = PoePlatformFactory()
        platform_string = factory.__get_platform_string(bootcmd_path)
        # Import the platform module based on the bootcmd string.
        if platform_string is not None:
            return (factory.__load_poe_plat(factory.__get_platform_module_path(platform_string)), platform_string)

        return (None, None)

    def __get_platform_string(self, bootcmd_path: str) -> str | None:
        """Extract the platform string from /etc/onl/platform if available, or the bootcmd file otherwise.
        Args:
            bootcmd_path (str): Path to the bootcmd file

        Returns:
            str | None: Platform string, if successful
        """

        try:
            with open(AgentConstants.ONL_PLATFORM_PATH, "r") as fh:
                return fh.read().rstrip()
        except FileNotFoundError:
            self.log.warn(
                "Couldn't find platform file %s, falling back to kernel cmdline" % AgentConstants.ONL_PLATFORM_PATH
            )

            try:
                with open(bootcmd_path, "r") as f:
                    d = dict()
                    for arg in f.read().split(" "):
                        # this test is necessary to avoid choking on args like "rw"
                        # we are choosing to not store such args in the dict
                        if "=" in arg:
                            key, value = arg.split("=")
                            d[key] = value
                    self._log.dbg(f"onl_platform: {d.get('onl_platform')}")
                    return d.get("onl_platform").rstrip()
            except Exception as e:
                self._log.crit(f"Failed to get the platform string: {e}")

        return None

    def __get_platform_module_path(self, platform: str) -> str:
        """Build the platform HAL module path

        Args:
            platform (str): The platform string

        Returns:
            str: The module path
        """
        # dentOS platform format: <arch>-<manufacturer>-<model>-<revision>
        [_, _, model_revision] = platform.replace("_", "-").split("-", 2)
        model_revision = model_revision.replace("-", "_")
        py_path = "/".join([AgentConstants.PLAT_VENDOR_PATH, f"{model_revision}.py"])
        self._log.dbg(f"Platform HAL module path: {py_path}")
        return py_path

    def __load_poe_plat(self, platform_py_path: str) -> PoePlatform | None:
        """Programmatically import the platform module and instantiate a HAL

        Args:
            platform_py_path (str): Path to the platform module

        Returns:
            PoePlatform | None: The platform HAL, if successful
        """
        if os.path.exists(platform_py_path):
            poe_plat = None
            try:
                spec = importlib.util.spec_from_file_location("poe_plat", platform_py_path)
                poe_plat = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(poe_plat)
                poe_plat = poe_plat.get_poe_platform()
                return poe_plat
            except Exception as e:
                print_stderr(f"Failed to instantiate the PoE platform HAL: {e}")
                raise
        else:
            print_stderr(f"No PoE platform found at {platform_py_path}, assuming no PoE support.")

        return None