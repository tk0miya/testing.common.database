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

import os
import sys
import signal
import socket
import tempfile
import subprocess
from time import sleep
from shutil import copytree, rmtree
from datetime import datetime


class DatabaseFactory(object):
    target_class = None

    def __init__(self, **kwargs):
        self.cache = None
        self.settings = kwargs

        init_handler = self.settings.pop('on_initialized', None)
        if self.settings.pop('cache_initialized_db', None):
            if init_handler:
                try:
                    self.cache = self.target_class()
                    init_handler(self.cache)
                except:
                    if self.cache:
                        self.cache.stop()
                    raise
                finally:
                    if self.cache:
                        self.cache.terminate()
            else:
                self.cache = self.target_class(auto_start=0)
                self.cache.setup()
            self.settings['copy_data_from'] = self.cache.get_data_directory()

    def __call__(self):
        return self.target_class(**self.settings)

    def clear_cache(self):
        if self.cache:
            self.settings['copy_data_from'] = None
            self.cache.cleanup()


class Database(object):
    BOOT_TIMEOUT = 10.0
    DEFAULT_SETTINGS = {}
    subdirectories = []

    def __init__(self, **kwargs):
        self.name = self.__class__.__name__
        self.settings = dict(self.DEFAULT_SETTINGS)
        self.settings.update(kwargs)
        self.child_process = None
        self._owner_pid = os.getpid()
        self._use_tmpdir = False

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
        except:
            self.cleanup()
            raise

    def initialize(self):
        pass

    def setup(self):
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
        except:
            self.cleanup()
            raise

    def get_data_directory(self):
        pass

    def initialize_database(self):
        pass

    def start(self):
        if self.child_process:
            return  # already started

        self.prestart()

        logger = open(os.path.join(self.base_dir, '%s.log' % self.name), 'wt')
        try:
            command = self.get_server_commandline()
            self.child_process = subprocess.Popen(command, stdout=logger, stderr=logger)
        except Exception as exc:
            raise RuntimeError('failed to launch %s: %r' % (self.name, exc))
        else:
            try:
                self.wait_booting()
                self.poststart()
            except:
                self.stop()
                raise
        finally:
            logger.close()

    def get_server_commandline(self):
        raise NotImplemented

    def wait_booting(self):
        exec_at = datetime.now()
        while True:
            if self.child_process.poll() is not None:
                raise RuntimeError("*** failed to launch %s ***\n" % self.name +
                                   self.read_bootlog())

            if self.is_server_available():
                break

            if (datetime.now() - exec_at).seconds > self.BOOT_TIMEOUT:
                raise RuntimeError("*** failed to launch %s (timeout) ***\n" % self.name +
                                   self.read_bootlog())

            sleep(0.1)

    def prestart(self):
        if self.settings['port'] is None:
            self.settings['port'] = get_unused_port()

    def poststart(self):
        pass

    def is_server_available(self):
        return False

    def is_alive(self):
        return self.child_process and self.child_process.poll() is None

    @property
    def server_pid(self):
        return getattr(self.child_process, 'pid', None)

    def stop(self, _signal=signal.SIGTERM):
        try:
            self.terminate(_signal)
        finally:
            self.cleanup()

    def terminate(self, _signal=signal.SIGTERM):
        if self.child_process is None:
            return  # not started

        if self._owner_pid != os.getpid():
            return  # could not stop in child process

        try:
            self.child_process.send_signal(_signal)
            killed_at = datetime.now()
            while self.child_process.poll() is None:
                if (datetime.now() - killed_at).seconds > 10.0:
                    os.child_process.kill()
                    raise RuntimeError("*** failed to shutdown postgres (timeout) ***\n" + self.read_bootlog())

                sleep(0.1)
        except OSError:
            pass

        self.child_process = None

    def cleanup(self):
        if self.child_process is not None:
            return

        try:
            if self._use_tmpdir and os.path.exists(self.base_dir):
                rmtree(self.base_dir, ignore_errors=True)
                self._use_tmpdir = False
        except (AttributeError, TypeError):
            pass

    def read_bootlog(self):
        try:
            with open(os.path.join(self.base_dir, '%s.log' % self.name)) as log:
                return log.read()
        except Exception as exc:
            raise RuntimeError("failed to open file:%s.log: %r" % (self.name, exc))

    def __del__(self):
        try:
            self.stop()
        except:
            errmsg = ('ERROR: testing.common.database: failed to shutdown the server automatically.\n'
                      'Any server processes and files might have been leaked. Please remove them and '
                      'call the stop() certainly')
            try:
                sys.__stderr__.write(errmsg)
            except:
                # if sys module is already unloaded by GC
                print errmsg

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.stop()


class SkipIfNotInstalledDecorator(object):
    name = ''

    def search_server(self):
        pass  # raise exception if not found

    def __call__(self, arg=None):
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
                except:
                    cond = True  # not found

            return skipIf(cond, "%s not found" % self.name)(fn)

        if callable(arg):  # execute as simple decorator
            return decorator(arg, None)
        else:  # execute with path argument
            return decorator


def get_unused_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(('localhost', 0))
    _, port = sock.getsockname()
    sock.close()

    return port


def get_path_of(name):
    path = subprocess.Popen(['/usr/bin/which', name],
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE).communicate()[0]
    if path:
        return path.rstrip().decode('utf-8')
    else:
        return None
