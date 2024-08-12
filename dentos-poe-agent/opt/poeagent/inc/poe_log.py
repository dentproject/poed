'''
Copyright Amazon Inc.

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


import sys
import syslog
import traceback

from singleton_thread_safe import SingletonThreadSafe


class PoeLog(object, metaclass=SingletonThreadSafe):
    """Syslog-based wrapper"""

    def __init__(self, debug_mode: bool = False) -> None:
        self.debug_mode = debug_mode

    def emerg(self, msg: str) -> None:
        self.__record(syslog.LOG_EMERG, "EMERG: %s" % msg)

    def alert(self, msg: str) -> None:
        self.__record(syslog.LOG_ALERT, "ALERT: %s" % msg)

    def crit(self, msg: str) -> None:
        self.__record(syslog.LOG_CRIT, "CRIT: %s" % msg)

    def err(self, msg: str) -> None:
        self.__record(syslog.LOG_ERR, "ERR: %s" % msg)

    def warn(self, msg: str) -> None:
        self.__record(syslog.LOG_WARNING, "WARN: %s" % msg)

    def notice(self, msg: str) -> None:
        self.__record(syslog.LOG_NOTICE, "NOTICE: %s" % msg)

    def info(self, msg: str) -> None:
        self.__record(syslog.LOG_INFO, "INFO: %s" % msg)

    def dbg(self, msg: str) -> None:
        self.__record(syslog.LOG_DEBUG, "DBG: %s" % msg)

    def exc(self, msg: str) -> None:
        """Log an error message beside the current exception message as an
        error

        Args:
            msg (string): Error message to log
        """
        if sys.exc_info()[0] is not None:
            for line in traceback.format_exc().splitlines():
                self.err(line)
        self.err(msg)

    def __record(self, priority: int, msg: str) -> None:
        """Forward the priority and the message to syslog

        Args:
            priority (integer): Log priority
            msg (string): Log message
        """
        syslog.syslog(priority, msg)
        if self.debug_mode:
            sys.stdout.write(msg + "\n")
