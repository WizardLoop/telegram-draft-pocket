# Copyright 2017 Google Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Exact RunTest method excerpt from xctest_session.py at
# https://github.com/google/xctestrunner/blob/4c6f4931aa65b3e6573b3ca11753c98fc8d3071c/xctestrunner/test_runner/xctest_session.py
# Kept only to prove failed XCTest exit codes survive attachment export.

def RunTest(self, device_id, os_version=None):
  """Runs test on the target device with the given device_id.

  Args:
    device_id: string, id of the device.
    os_version: string, OS version of the device.

  Returns:
    A value of type runner_exit_codes.EXITCODE.

  Raises:
    XcodebuildTestError: when the XctestSession.Prepare has not been called.
  """
  if not self._prepared:
    raise ios_errors.XcodebuildTestError(
        'The session has not been prepared. Please call '
        'XctestSession.Prepare first.')

  if self._xctestrun_obj:
    result_bundle_path = os.path.join(self._output_dir, 'test.xcresult')
    exit_code = self._xctestrun_obj.Run(device_id, self._sdk,
                                        self._output_dir,
                                        self._startup_timeout_sec,
                                        self._destination_timeout_sec,
                                        os_version=os_version,
                                        result_bundle_path=result_bundle_path)
    # The xcresult only contains raw data in Xcode 11 or later.
    if xcode_info_util.GetXcodeVersionNumber() >= 1100:
      expose_xcresult = os.path.join(self._output_dir, 'ExposeXcresult')
      try:
        xcresult_util.ExposeXcresult(result_bundle_path, expose_xcresult)
        if not self._keep_xcresult_data:
          shutil.rmtree(result_bundle_path)
      except subprocess.CalledProcessError as e:
        logging.warning(e.output)
    return exit_code
  elif self._logic_test_bundle:
    return logic_test_util.RunLogicTestOnSim(
        device_id,
        self._logic_test_bundle,
        self._logic_test_env_vars,
        self._logic_test_args,
        self._logic_tests_to_run,
        os_version=os_version)
  else:
    raise ios_errors.XcodebuildTestError('Unexpected runtime error.')
