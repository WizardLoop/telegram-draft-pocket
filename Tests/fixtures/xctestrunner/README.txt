These small Apache-2.0 fixtures preserve upstream code for an executable
regression of the optional demo runner patch. They are not an alternate runner.

Source commit: 4c6f4931aa65b3e6573b3ca11753c98fc8d3071c
Repository: https://github.com/google/xctestrunner

xcresult_util.py is the complete 5.8 KB pinned file, unchanged:
https://github.com/google/xctestrunner/blob/4c6f4931aa65b3e6573b3ca11753c98fc8d3071c/xctestrunner/test_runner/xcresult_util.py
SHA-256: 996b0662137116cb05809afd46c2422f46bb33da15345c3b118119f1bd2f8891

session_run_test.py contains only the exact RunTest method from xctest_session.py,
dedented for execution, with the original license header retained:
https://github.com/google/xctestrunner/blob/4c6f4931aa65b3e6573b3ca11753c98fc8d3071c/xctestrunner/test_runner/xctest_session.py

The tests apply the authored dependency patch to the pinned file using Git,
execute its real export functions with an empty Array representation observed
on Xcode 27.1, and execute the pinned caller to retain the failed XCTest code.

The complete upstream LICENSE is included unchanged from the same pinned commit:
https://github.com/google/xctestrunner/blob/4c6f4931aa65b3e6573b3ca11753c98fc8d3071c/LICENSE
SHA-256: 58d1e17ffe5109a7ae296caafcadfdbe6a7d176f0bc4ab01e12a689b0499d8bd
Git blob: 7a4a3ea2424c09fbe48d455aed1eaa94d9124835
The complete pinned repository tree contains no NOTICE file.
