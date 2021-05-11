# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import recipe_api


class AddhocValidationApi(recipe_api.RecipeApi):
  """Wrapper api to run bash scripts as validation in LUCI builder steps.

  This api expects all the bash or bat scripts to exist in its resources
  directory and also expects the validation name to be listed in
  available_validations method.
  """

  def available_validations(self):
    """Returns the list of accepted validations."""
    return [
        'analyze', 'customer_testing', 'docs', 'fuchsia_precache',
        'verify_binaries_codesigned', 'validate_ci_config',
    ]

  def run(self, name, validation, env, env_prefixes, secrets=None):
    """Runs a validation as a recipe step.

    Args:
      name(str): The step group name.
      validation(str): The name of a validation to run. This has to correlate
        to a <validation>.sh for linux/mac or <validation>.bat for windows.
      env(dict): Current environment variables.
      env_prefixes(dict):  Current environment prefixes variables.
      secrets(dict): The key is the name of the secret and value is the path to kms.
    """
    if validation not in self.available_validations():
      msg = validation + ' is not listed in available_validations.'
      raise AssertionError(msg)
    secrets = secrets or {}
    with self.m.step.nest(name):
      resource_name = ''
      deps = self.m.properties.get('dependencies', [])
      self.m.flutter_deps.required_deps(env, env_prefixes, deps)
      self.m.kms.decrypt_secrets(env, secrets)
      if self.m.platform.is_linux or self.m.platform.is_mac:
        resource_name = self.resource('%s.sh' % validation)
        self.m.step(
            'Set execute permission',
            ['chmod', '755', resource_name],
            infra_step=True,
        )
      elif self.m.platform.is_win:
        resource_name = self.resource('%s.bat' % validation)
      dep_list = [d['dependency'] for d in deps]
      checkout_path = self.m.repo_util.sdk_checkout_path()
      if 'xcode' in dep_list:
        with self.m.osx_sdk('ios'):
          self.m.flutter_deps.gems(
              env, env_prefixes, checkout_path.join('dev', 'ci', 'mac')
          )
          with self.m.context(env=env, env_prefixes=env_prefixes):
            self.m.test_utils.run_test(validation, [resource_name])
      else:
        with self.m.context(env=env, env_prefixes=env_prefixes):
          self.m.test_utils.run_test(validation, [resource_name])
          if validation == 'docs' and self.m.properties.get('firebase_project'):
            docs_path = checkout_path.join('dev', 'docs')
            project = self.m.properties.get('firebase_project')
            self.m.firebase.deploy_docs(
                env=env,
                env_prefixes=env_prefixes,
                docs_path=docs_path,
                project=project
            )
