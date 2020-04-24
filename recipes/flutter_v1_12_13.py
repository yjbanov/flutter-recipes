# Copyright 2016 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

# This file is a copy of flutter.py from
# 62f52b6e2a50f2df3ec81509f93c578847e03947, or the version of the recipe at
# the time v1.12.13 was tagged.

from contextlib import contextmanager
import re

DEPS = [
    'build',
    'depot_tools/git',
    'depot_tools/gsutil',
    'depot_tools/depot_tools',
    'depot_tools/osx_sdk',
    'depot_tools/windows_sdk',
    'recipe_engine/buildbucket',
    'recipe_engine/cipd',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/python',
    'recipe_engine/raw_io',
    'recipe_engine/runtime',
    'recipe_engine/step',
    'recipe_engine/url',
    'zip',
]

BUCKET_NAME = 'flutter_infra'
PACKAGED_REF_RE = re.compile(r'^refs/heads/(dev|beta|stable)$')


@contextmanager
def _PlatformSDK(api):
  if api.platform.is_win:
    with api.windows_sdk():
      with InstallOpenJDK(api):
        yield
  elif api.platform.is_mac:
    yield
  elif api.platform.is_linux:
    with InstallOpenJDK(api):
      yield


@contextmanager
def Install7za(api):
  if api.platform.is_win:
    sevenzip_cache_dir = api.path['cache'].join('builder', '7za')
    api.cipd.ensure(
        sevenzip_cache_dir,
        api.cipd.EnsureFile().add_package(
            'flutter_internal/tools/7za/${platform}', 'version:19.00'))
    with api.context(env_prefixes={'PATH': [sevenzip_cache_dir]}):
      yield
  else:
    yield


def InstallOpenJDK(api):
  java_cache_dir = api.path['cache'].join('java')
  api.cipd.ensure(
      java_cache_dir,
      api.cipd.EnsureFile().add_package(
          'flutter_internal/java/openjdk/${platform}', 'version:1.8.0u202-b08'))
  return api.context(
      env={'JAVA_HOME': java_cache_dir},
      env_prefixes={'PATH': [java_cache_dir.join('bin')]})


def EnsureCloudKMS(api, version=None):
  with api.step.nest('ensure_cloudkms'):
    with api.context(infra_steps=True):
      pkgs = api.cipd.EnsureFile()
      pkgs.add_package('infra/tools/luci/cloudkms/${platform}', version or
                       'latest')
      cipd_dir = api.path['start_dir'].join('cipd', 'cloudkms')
      api.cipd.ensure(cipd_dir, pkgs)
      return cipd_dir.join('cloudkms')


def DecryptKMS(api, step_name, crypto_key_path, ciphertext_file,
               plaintext_file):
  kms_path = EnsureCloudKMS(api)
  return api.step(step_name, [
      kms_path,
      'decrypt',
      '-input',
      ciphertext_file,
      '-output',
      plaintext_file,
      crypto_key_path,
  ])


def GetCloudPath(api, git_hash, path):
  if api.runtime.is_experimental:
    return 'flutter/experimental/%s/%s' % (git_hash, path)
  return 'flutter/%s/%s' % (git_hash, path)


def UploadFlutterCoverage(api):
  """Uploads the Flutter coverage output to cloud storage and Coveralls.
  """
  if not api.properties.get('upload_packages', False):
    return

  # Upload latest coverage to cloud storage.
  checkout = api.path['checkout']
  flutter_package_dir = checkout.join('packages', 'flutter')
  coverage_path = flutter_package_dir.join('coverage', 'lcov.info')
  api.gsutil.upload(
      coverage_path,
      BUCKET_NAME,
      GetCloudPath(api, 'coverage', 'lcov.info'),
      link_name='lcov.info',
      name='upload coverage data')

  token_path = flutter_package_dir.join('.coveralls.yml')
  DecryptKMS(api, 'decrypt coveralls token',
          'projects/flutter-infra/locations/global' \
          '/keyRings/luci/cryptoKeys/coveralls',
          api.resource('coveralls-token.enc'),
          token_path)
  pub_executable = 'pub' if not api.platform.is_win else 'pub.exe'
  api.step('pub global activate coveralls', [
      pub_executable, 'global', 'activate', 'coveralls', '5.1.0',
      '--no-executables'
  ])
  with api.context(cwd=flutter_package_dir):
    api.step('upload to coveralls',
             [pub_executable, 'global', 'run', 'coveralls:main', coverage_path])


def CreateAndUploadFlutterPackage(api, git_hash, branch):
  """Prepares, builds, and uploads an all-inclusive archive package."""
  # For creating the packages, we need to have the master branch version of the
  # script, but we need to know what the revision in git_hash is first. So, we
  # end up checking out the flutter repo twice: once on the branch we're going
  # to package, to find out the hash to use, and again here so that we have the
  # current version of the packaging script.
  api.git.checkout(
      'https://chromium.googlesource.com/external/github.com/flutter/flutter',
      ref='master',
      recursive=True,
      set_got_revision=True)

  flutter_executable = 'flutter' if not api.platform.is_win else 'flutter.bat'
  dart_executable = 'dart' if not api.platform.is_win else 'dart.exe'
  work_dir = api.path['start_dir'].join('archive')
  prepare_script = api.path['checkout'].join('dev', 'bots',
                                             'prepare_package.dart')
  api.step('flutter doctor', [flutter_executable, 'doctor'])
  api.step('download dependencies', [flutter_executable, 'update-packages'])
  api.file.rmtree('clean archive work directory', work_dir)
  api.file.ensure_directory('(re)create archive work directory', work_dir)
  with Install7za(api):
    with api.context(cwd=api.path['start_dir']):
      step_args = [
          dart_executable, prepare_script,
          '--temp_dir=%s' % work_dir,
          '--revision=%s' % git_hash,
          '--branch=%s' % branch
      ]
      if not api.runtime.is_experimental:
        step_args.append('--publish')
      api.step('prepare, create and publish a flutter archive', step_args)


def RunSteps(api):
  git_url = \
    'https://chromium.googlesource.com/external/github.com/flutter/flutter'
  git_ref = api.buildbucket.gitiles_commit.ref
  if ('git_url' in api.properties and 'git_ref' in api.properties):
    git_url = api.properties['git_url']
    git_ref = api.properties['git_ref']

  git_hash = api.git.checkout(
      git_url, ref=git_ref, recursive=True, set_got_revision=True, tags=True)
  checkout = api.path['checkout']

  dart_bin = checkout.join('bin', 'cache', 'dart-sdk', 'bin')
  flutter_bin = checkout.join('bin')

  path_prefixes = [
      flutter_bin,
      dart_bin,
  ]

  env_prefixes = {'PATH': path_prefixes}

  # TODO(eseidel): This is named exactly '.pub-cache' as a hack around
  # a regexp in flutter_tools analyze.dart which is in turn a hack around:
  # https://github.com/dart-lang/sdk/issues/25722
  pub_cache = checkout.join('.pub-cache')
  env = {
      # Setup our own pub_cache to not affect other slaves on this machine,
      # and so that the pre-populated pub cache is contained in the package.
      'PUB_CACHE': pub_cache,
      # Windows Packaging script assumes this is set.
      'DEPOT_TOOLS': str(api.depot_tools.root),
  }

  flutter_executable = 'flutter' if not api.platform.is_win else 'flutter.bat'
  dart_executable = 'dart' if not api.platform.is_win else 'dart.exe'

  with api.context(env=env, env_prefixes=env_prefixes):
    with api.depot_tools.on_path():
      if git_ref:
        match = PACKAGED_REF_RE.match(git_ref)
        if match:
          branch = match.group(1)
          CreateAndUploadFlutterPackage(api, git_hash, branch)
          # Nothing left to do on a packaging branch.
          return

  # The context adds dart-sdk tools to PATH and sets PUB_CACHE.
  with api.context(env=env, env_prefixes=env_prefixes, cwd=checkout):
    api.step('flutter doctor', [flutter_executable, 'doctor'])
    api.step('download dependencies', [flutter_executable, 'update-packages'])

  with _PlatformSDK(api):
    with api.context(env=env, env_prefixes=env_prefixes, cwd=checkout):
      shard = api.properties['shard']
      shard_env = env
      shard_env['SHARD'] = shard
      with api.context(env=shard_env):
        api.step('run test.dart for %s shard' % shard,
                 [dart_executable,
                  checkout.join('dev', 'bots', 'test.dart')])
      if shard == 'coverage':
        UploadFlutterCoverage(api)
      # Windows uses exclusive file locking.  On LUCI, if these processes remain
      # they will cause the build to fail because the builder won't be able to
      # clean up.
      # This might fail if there's not actually a process running, which is
      # fine.
      # If it actually fails to kill the task, the job will just fail anyway.
      if api.platform.is_win:

        def KillAll(name, exe_name):
          api.step(
              name, ['taskkill', '/f', '/im', exe_name, '/t'], ok_ret='any')

        KillAll('stop gradle daemon', 'java.exe')
        KillAll('stop dart', 'dart.exe')
        KillAll('stop adb', 'adb.exe')


def GenTests(api):
  for experimental in (True, False):
    for should_upload in (True, False):
      yield api.test(
          'linux_master_coverage_%s%s' %
          ('_experimental' if experimental else '',
           '_upload' if should_upload else ''),
          api.runtime(is_luci=True, is_experimental=experimental),
          api.properties(
              shard='coverage',
              coveralls_lcov_version='5.1.0',
              upload_packages=should_upload),
      )
      for platform in ('mac', 'linux', 'win'):
        for branch in ('master', 'dev', 'beta', 'stable'):
          git_ref = 'refs/heads/' + branch
          test = api.test(
              '%s_%s%s%s' % (platform, branch, '_experimental' if experimental
                             else '', '_upload' if should_upload else ''),
              api.platform(platform, 64),
              api.buildbucket.ci_build(git_ref=git_ref, revision=None),
              api.properties(shard='tests', upload_packages=should_upload),
              api.runtime(is_luci=True, is_experimental=experimental),
          )
          yield test

  yield api.test(
      'pull_request',
      api.runtime(is_luci=True, is_experimental=True),
      api.properties(
          git_url='https://github.com/flutter/flutter',
          git_ref='refs/pull/1/head',
          shard='tests',
          should_upload=False),
  )