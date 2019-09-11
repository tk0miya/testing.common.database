# -*- coding: utf-8 -*-
#  Copyright 2013 Takeshi KOMIYA
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
"""Provide base classes and utility functions for using in testing.* packages."""

import copy
import os
import sys
import signal
import socket
import tempfile
import subprocess
from time import sleep
from timeit import default_timer as timestamp
from shutil import copytree, rmtree
import collections


class DatabaseFactory(object):
    """DatabaseFactory is an object which can produce instances of a database."""

    target_class = None

    def __init__(self, **kwargs):
        """Initialize a DatabaseFactory instance."""
        self.cache = None
        self.settings = kwargs

        init_handler = self.settings.pop('on_initialized', None)
        if self.settings.pop('cache_initialized_db', None):
            if init_handler:
                try:
                    self.cache = self.target_class(**self.settings)
                    init_handler(self.cache)
                except Exception:
                    if self.cache:
                        self.cache.stop()
                    raise
                finally:
                    if self.cache:
                        self.cache.terminate()
            else:
                settings_noautostart = copy.deepcopy(self.settings)
                settings_noautostart.update({"auto_start": 0})
                self.cache = self.target_class(**settings_noautostart)
                self.cache.setup()
            self.settings['copy_data_from'] = self.cache.get_data_directory()

    def __call__(self):
        """Create and return an instance of a Database made by this factory."""
        return self.target_class(**self.settings)

    def clear_cache(self):
        """Clear the cache of this DatabaseFactory."""
        if self.cache:
            self.settings['copy_data_from'] = None
            self.cache.cleanup()


class Database(object):
    """Represents an instance of a database process."""

    DEFAULT_BOOT_TIMEOUT = 10.0
    DEFAULT_KILL_TIMEOUT = 10.0
    DEFAULT_SETTINGS = {}
    subdirectories = []
    terminate_signal = signal.SIGTERM

    def __init__(self, **kwargs):
        """Create an instance of a Database."""
        self.name = self.__class__.__name__
        self.settings = dict(self.DEFAULT_SETTINGS)
        self.settings.update(kwargs)
        self.child_process = None
        self._owner_pid = os.getpid()
        self._use_tmpdir = False

        if os.name == 'nt':
            self.terminate_signal = signal.CTRL_BREAK_EVENT

        self.base_dir = self.settings.pop('base_dir')
        if self.base_dir:
            if self.base_dir[0] != '/':
                self.base_dir = os.path.join(os.getcwd(), self.base_dir)
        else:
            self.base_dir = tempfile.mkdtemp()
            self._use_tmpdir = True

        try:
            self.initialize()

            if self.settings['auto_start']:
                if self.settings['auto_start'] >= 2:
                    self.setup()

                self.start()
        except Exception:
            self.cleanup()
            raise

    def initialize(self):
        """Initialize database object."""
        pass

    def setup(self):
        """Copy datafiles and prepare execution environment."""
        # copy data files
        if self.settings['copy_data_from']:
            try:
                data_dir = self.get_data_directory()
                copytree(self.settings['copy_data_from'], data_dir)
                os.chmod(data_dir, 0o700)
            except Exception as exc:
                raise RuntimeError("could not copytree %s to %s: %r" %
                                   (self.settings['copy_data_from'], data_dir, exc))

        # create directory tree
        for subdir in self.subdirectories:
            path = os.path.join(self.base_dir, subdir)
            if not os.path.exists(path):
                os.makedirs(path)
                os.chmod(path, 0o700)

        try:
            self.initialize_database()
        except Exception:
            self.cleanup()
            raise

    def get_data_directory(self):
        """Return path to data directory of database."""
        pass

    def initialize_database(self):
        """Initialize database server (not the object)."""
        pass

    def start(self):
        """Do necessary setup, and start database server."""
        if self.child_process:
            return  # already started

        self.prestart()

        logger = open(os.path.join(self.base_dir, '%s.log' % self.name), 'wt')
        try:
            command = self.get_server_commandline()
            flags = 0
            if os.name == 'nt':
                flags |= subprocess.CREATE_NEW_PROCESS_GROUP
            self.child_process = subprocess.Popen(command, stdout=logger, stderr=logger,
                                                  creationflags=flags)
        except Exception as exc:
            raise RuntimeError('failed to launch %s: %r' % (self.name, exc))
        else:
            try:
                self.wait_booting()
                self.poststart()
            except Exception:
                self.stop()
                raise
        finally:
            logger.close()

    def get_server_commandline(self):
        """Command line to invoke your database server."""
        raise NotImplementedError

    def wait_booting(self):
        """Wait for the database server process start and be available."""
        boot_timeout = self.settings.get('boot_timeout', self.DEFAULT_BOOT_TIMEOUT)
        exec_at = timestamp()
        while True:
            if self.child_process.poll() is not None:
                raise RuntimeError(
                    "*** failed to launch %s ***\n" % self.name + self.read_bootlog()
                )

            if self.is_server_available():
                break

            if (timestamp() - exec_at) > boot_timeout:
                raise RuntimeError(
                    "*** failed to launch %s (timeout) ***\n" % self.name + self.read_bootlog()
                )

            sleep(0.1)

    def prestart(self):
        """Perform any actions which are necessary before the database server is started."""
        if self.settings['port'] is None:
            self.settings['port'] = get_unused_port()

    def poststart(self):
        """Perform necessary actions after the database is started, before it is ready to use."""
        pass

    def is_server_available(self):
        """Return True if the database is ready to accept connections, otherwise False."""
        return False

    def is_alive(self):
        """Return boolean for is the database running (may not be ready to accept connections)."""
        return self.child_process and self.child_process.poll() is None

    @property
    def server_pid(self):
        """Return the process id of the database server process."""
        return getattr(self.child_process, 'pid', None)

    def stop(self, _signal=signal.SIGTERM):
        """Send _signal to child process and cleanup temporary directory."""
        try:
            self.terminate(_signal)
        finally:
            self.cleanup()

    def terminate(self, _signal=None):
        """Send _signal to child process and wait for it to exit raising RuntimeError if it does not."""
        if self.child_process is None:
            return  # not started

        if self._owner_pid != os.getpid():
            return  # could not stop in child process

        if _signal is None:
            _signal = self.terminate_signal

        try:
            self.child_process.send_signal(_signal)
            killed_at = timestamp()
            while self.child_process.poll() is None:
                if (timestamp() - killed_at) > self.DEFAULT_KILL_TIMEOUT:
                    self.child_process.kill()
                    raise RuntimeError("*** failed to shutdown postgres (timeout) ***\n" + self.read_bootlog())

                sleep(0.1)
        except OSError:
            pass

        self.child_process = None

    def cleanup(self):
        """Cleanup any temporary files from disk."""
        if self.child_process is not None:
            return

        if self._use_tmpdir and os.path.exists(self.base_dir):
            rmtree(self.base_dir, ignore_errors=True)
            self._use_tmpdir = False

    def read_bootlog(self):
        """Return the contents of the database log as a string."""
        try:
            with open(os.path.join(self.base_dir, '%s.log' % self.name)) as log:
                return log.read()
        except Exception as exc:
            raise RuntimeError("failed to open file:%s.log: %r" % (self.name, exc))

    def __del__(self):
        """When there are no remaining references to the database, cleanup."""
        try:
            self.stop()
        except Exception:
            errmsg = ('ERROR: testing.common.database: failed to shutdown the server automatically.\n'
                      'Any server processes and files might have been leaked. Please remove them and '
                      'call the stop() certainly')
            try:
                sys.__stderr__.write(errmsg)
            except Exception:
                # if sys module is already unloaded by GC
                print(errmsg)

    def __enter__(self):
        """Entry point for database as a context manager (with database() as ...)."""
        return self

    def __exit__(self, *args):
        """Stop database and cleanup when exiting with block."""
        self.stop()


class SkipIfNotInstalledDecorator(object):
    """Decorator that skips the testcase if a database command is not found."""

    name = ''

    def search_server(self):
        """Return some x such that bool(x) is truthy iff the server is found."""
        pass  # raise exception if not found

    def __call__(self, arg=None):
        """Return a decorator which skips decorated function if the required server is not present."""
        if sys.version_info < (2, 7):
            from unittest2 import skipIf
        else:
            from unittest import skipIf

        def decorator(fn, path=arg):
            if path:
                cond = not os.path.exists(path)
            else:
                try:
                    self.search_server()
                    cond = False  # found
                except Exception:
                    cond = True  # not found

            return skipIf(cond, "%s not found" % self.name)(fn)

        if isinstance(arg, collections.Callable):  # execute as simple decorator
            return decorator(arg, None)
        else:  # execute with path argument
            return decorator


def get_unused_port():
    """Return a random unused port."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(('localhost', 0))
    _, port = sock.getsockname()
    sock.close()
    return port


def get_path_of(name):
    """Return the path to the given executable, or None if not found."""
    if os.name == 'nt':
        which = 'where'
    else:
        which = 'which'
    try:
        path = subprocess.Popen([which, name],
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE).communicate()[0]
        if path:
            return path.rstrip().decode('utf-8')
        else:
            return None
    except Exception:
        return None
