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

from abc import ABC, abstractmethod

class BusDriver(ABC):
    """Bus driver interface used for accessing the PoE chipset
    through the system interconnect (e.g. I2c)
    """

    @abstractmethod
    def write_message(self, msg: list, delay: int) -> None:
        pass

    @abstractmethod
    def read_message(self) -> list:
        pass

    @abstractmethod
    def read(self, size) -> list:
        pass

    @abstractmethod
    def bus_lock(self) -> None:
        pass

    @abstractmethod
    def bus_unlock(self) -> None:
        pass
