load("@build_bazel_rules_swift//swift:swift.bzl", "swift_library")
load("@build_bazel_rules_apple//apple:ios.bzl", "ios_ui_test")
load("@build_bazel_rules_apple//apple/testing/default_runner:ios_test_runner.bzl", "ios_test_runner")

ios_test_runner(
    name = "DraftPocketDemoRunner",
    device_type = "iPhone 17",
    os_version = "27.0",
)

swift_library(
    name = "DraftPocketDemoUITestsLib",
    testonly = True,
    srcs = ["DraftPocketDemoUITests.swift"],
)

ios_ui_test(
    name = "DraftPocketDemoUITests",
    size = "large",
    minimum_os_version = "15.0",
    runner = ":DraftPocketDemoRunner",
    test_host = "//Telegram:Telegram",
    deps = [":DraftPocketDemoUITestsLib"],
    visibility = ["//visibility:public"],
)
