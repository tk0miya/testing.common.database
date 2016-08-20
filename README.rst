About
=====
``testing.common.database`` is utilities for testing.* package.

.. image:: https://travis-ci.org/tk0miya/testing.common.database.svg?branch=master
   :target: https://travis-ci.org/tk0miya/testing.common.database

.. image:: https://codeclimate.com/github/tk0miya/testing.common.database/badges/gpa.svg
   :target: https://codeclimate.com/github/tk0miya/testing.common.database


Install
=======
Use pip::

   $ pip install testing.common.database


Helpers
=======
class Database(object):

    ``Database`` is a base class for database testing packages.
    To create your database testing class, inherit this class and override methods below.

    def initialize(self):

        Handler for initialize database object.

    def get_data_directory(self):

        Path to data directory of your databse.

        Example::

          def get_data_directory(self):
              return os.path.join(self.base_dir, 'data')

    def initialize_database(self):

        Handler to initialize your database.

        Example::

          def initialize_database(self):
             if not os.path.exists(os.path.join(self.base_dir, 'data', 'PG_VERSION')):
                 args = ([self.initdb, '-D', os.path.join(self.base_dir, 'data'), '--lc-messages=C'] +
                         self.settings['initdb_args'].split())

                 try:
                     p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                     output, err = p.communicate()
                     if p.returncode != 0:
                         raise RuntimeError("initdb failed: %r" % err)
                 except OSError as exc:
                     raise RuntimeError("failed to spawn initdb: %s" % exc)

    def get_server_commandline(self):

        Command line to invoke your database server.

        Example::

          def get_server_commandline(self):
              return (['postgres',
                       '-p', str(self.settings['port']),
                       '-D', os.path.join(self.base_dir, 'data'),
                       '-k', os.path.join(self.base_dir, 'tmp')] +
                      self.settings['postgres_args'].split())

    def prestart(self):

        Handler called before invoking your database server.

    def poststart(self):

        Hander called after invoking your database server.

    def is_server_available(self):

        Methods check your database server available.
        The ``Database`` class uses this method to check the server boots up.

        Example::

          try:
              with closing(pg8000.connect(**self.dsn(database='template1'))):
                  pass
          except pg8000.Error:
              return False
          else:
              return True

    def is_alive(self):

        Methods check the database server is alive.

    @property
    def server_pid(self):

        Process ID of the database server.


class DatabaseFactory(object):

    ``DatabaseFactory`` is a factory class for the database class.
    To create your database factory class, inherit this class and set ``target_class`` variable::

      class PostgresqlFactory(DatabaseFactory):
          target_class = Postgresql

    The factory class should work like a ``target_class``::

      # The factory class generates like a ``target_class``, in this case, generates ``Postgresql`` class
      Postgresql = PostgresqlFactory()

      # The generated class works same as ``target_class``
      with Postgresql() as pgsql:
          #
          # do any tests using the database ...
          #

    It can bypass parameters to the ``target_class`` on every instantiation::

      Postgresql = PostgresqlFactory(copy_data_from='/path/to/database')

      with Postgresql() as pgsql:
          #
          # Test with ``copy_data_from`` parameter :-)
          #

    Also, it is able to cache the database generated at ``Database.initialize_database()``
    with ``cache_initialized_db`` parameter.
    It avoids running database initialization on every tests::

      # Initialize database once
      Postgresql = PostgresqlFactory(cache_initialized_db=True)

      with Postgresql() as pgsql:
          # copy cached database for this test.

    If you want to fixtures to the database, use ``on_initialized`` parameter::

      def handler(pgsql):
          # inserting fixtures

      # Initialize database once, and call ``on_initialized`` handler
      Postgresql = PostgresqlFactory(cache_initialized_db=True,
                                     on_initialized=handler)

class SkipIfNotInstalledDecorator(object):

    Generates decorator that skips the testcase if database command not found.
    To create decorator, inherit this class and set ``name`` variable and override ``search_server()`` method.

    Example::

      class PostgresqlSkipIfNotInstalledDecorator(SkipIfNotInstalledDecorator):
          name = 'PostgreSQL'

          def search_server(self):
              find_program('postgres', ['bin'])  # raise exception if not found


      skipIfNotFound = skipIfNotInstalled = PostgresqlSkipIfNotInstalledDecorator()

      @skipIfNotFound
      def test():
          # testcase

def get_unused_port():

    Get free TCP port.

def get_path_of(name):

    Searchs command from search paths. It works like ``which`` command.


Requirements
============
* Python 2.6, 2.7, 3.2, 3.3, 3.4, 3.5

License
=======
Apache License 2.0


History
=======

2.0.0 (2016-08-20)
-------------------
* Use subprocess.Popen() instead of fork & exec
* Support windows platform (experimental)
* #4: Add boot_timeout parameter
* Fix bugs:

  - Fix syntax errors for Python3
  - Show error messages if rescue from GC failed (ref: #1)

1.1.0 (2016-02-05)
-------------------
* Add Database#server_pid to get pid of the database server
* Add Database#is_alive() to check server is alive
* Define BOOT_TIMEOUT as constant
* Fix AttributeError if any exceptions are raised in bootstrap

1.0.0 (2016-02-01)
-------------------
* Initial release
