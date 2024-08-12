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


import threading


class SingletonThreadSafe(type):
    _instances = {}
    _instance_locks = {}
    _singleton_lock = threading.Lock()

    def __call__(cls, *args, **kwargs):
        # double-checked locking pattern
        instance = (cls, frozenset(args), frozenset(kwargs.items()))
        if instance not in cls._instances:
            with cls._singleton_lock:
                lock = cls._instance_locks.setdefault(instance, threading.Lock())
            with lock:
                if instance not in cls._instances:
                    cls._instances[instance] = super(SingletonThreadSafe, cls).__call__(*args, **kwargs)
                    with cls._singleton_lock:
                        del cls._instance_locks[instance]
        return cls._instances[instance]
