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

import fcntl
import threading
import time
from typing import Iterable

from bus_driver import BusDriver
from smbus2 import SMBus, i2c_msg


class I2cDriver(BusDriver):
    """Common PoeDriver implementation for I2c-based designs."""

    I2C_WRITE_DELAY = 0.3

    def __init__(self, i2c_bus: int, i2c_addr: int):
        if i2c_bus is None:
            raise AssertionError("I2c bus must not be None")
        self._i2c_bus = i2c_bus
        if i2c_addr is None:
            raise AssertionError("I2c address must not be None")
        self._i2c_addr = i2c_addr
        self._poe_bus = SMBus(self.i2c_bus)
        self._poe_lock = threading.Lock()

    @property
    def i2c_bus(self) -> int:
        return self._i2c_bus

    @property
    def i2c_addr(self) -> int:
        return self._i2c_addr

    def __bus(self):
        if self._poe_bus.fd is None:
            self._poe_bus = SMBus(self._poe_bus)
        return self._poe_bus

    def __lock(self):
        if self._poe_lock is None:
            self._poe_lock = threading.Lock()
        return self._poe_lock

    def __i2c_write(self, bus, msg, delay=I2C_WRITE_DELAY):
        write = i2c_msg.write(self.i2c_addr, msg)
        bus.i2c_rdwr(write)
        time.sleep(delay)

    def __i2c_read(self, bus, size=15):
        result = i2c_msg.read(self.i2c_addr, size)
        bus.i2c_rdwr(result)

        if not isinstance(result, Iterable):
            raise AssertionError("The result for an I2c read must be iterable")
        return list(result)

    def write_message(self, msg: list, delay: int) -> None:
        self.__i2c_write(self.__bus(), msg, delay)

    def read_message(self) -> list:
        return self.__i2c_read(self.__bus())

    def read(self, size) -> list:
        return self.__i2c_read(self.__bus(), size)

    def bus_lock(self) -> None:
        self.__lock().acquire()
        fcntl.flock(self.__bus().fd, fcntl.LOCK_EX)

    def bus_unlock(self) -> None:
        fcntl.flock(self.__bus().fd, fcntl.LOCK_UN)
        self.__lock().release()
