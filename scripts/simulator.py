"""Own one fresh Simulator, bound its lifecycle, and preserve startup evidence."""

import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import uuid

import package


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class SimulatorSession:
    def __init__(self, artifacts, device_type="iPhone 17", os_version="27.0", label="native"):
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", label):
            raise package.PackageError("Invalid owned Simulator label")
        self.artifacts = Path(artifacts).resolve()
        self.artifacts.mkdir(parents=True, exist_ok=True)
        self.device_type, self.os_version, self.label = device_type, os_version, label
        self.udid, self.runtime = None, None
        self._owned = False
        self._restore_renderer = False
        self._cleanup_evidence_errors = []
        self.evidence = {"schema_version": 1, "label": label, "device_type": device_type,
                         "os_version": os_version, "created_utc": utc_now(), "phases": [], "health": []}

    def _save(self):
        (self.artifacts / "simulator-lifecycle.json").write_text(
            json.dumps(self.evidence, indent=2) + "\n", encoding="utf-8")

    def _persist_command_evidence(self, action, name, cleanup):
        try:
            action()
        except OSError as error:
            if not cleanup:
                raise
            self._cleanup_evidence_errors.append(f"{name} evidence: {error}")

    def _run(self, name, command, timeout=30, *, public_output=True, allowed_codes=(0,), cleanup=False, cwd=None):
        phase = {"name": name, "started_utc": utc_now(), "timeout_seconds": timeout, "status": "running"}
        self.evidence["phases"].append(phase)
        self._persist_command_evidence(self._save, name, cleanup)
        started = time.monotonic()
        output = b""
        try:
            result = subprocess.run([str(arg) for arg in command], stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, timeout=timeout, check=False, cwd=cwd)
            output = result.stdout or b""
            phase["returncode"] = result.returncode
            if result.returncode not in allowed_codes:
                raise package.PackageError(f"Simulator {name} exited {result.returncode}; see its phase log")
            phase["status"] = "passed"
            return output
        except subprocess.TimeoutExpired as error:
            output = error.output or b""
            phase["status"] = "timed_out"
            raise package.PackageError(f"Simulator {name} exceeded {timeout}s; see its phase log") from None
        except (package.PackageError, OSError):
            phase["status"] = "failed"
            raise
        finally:
            phase["finished_utc"] = utc_now()
            phase["elapsed_seconds"] = round(time.monotonic() - started, 3)
            phase["output_bytes"] = len(output)
            if public_output:
                filename = f"simulator-{len(self.evidence['phases']):02d}-{name}.log"
                # Commands have no account/configuration arguments. Keep output bounded.
                self._persist_command_evidence(lambda: (self.artifacts / filename).write_bytes(output[:256 * 1024]), name, cleanup)
                phase["log"] = filename
                phase["output_truncated"] = len(output) > 256 * 1024
            self._persist_command_evidence(self._save, name, cleanup)

    def health(self, name):
        snapshot = {"name": name, "utc": utc_now(), "owned_udid": self.udid,
                    "disk_free_bytes": shutil.disk_usage(self.artifacts).free}
        for key, command in (("memory", ["/usr/sbin/sysctl", "hw.memsize", "vm.swapusage"]),
                             ("vm_stat", ["/usr/bin/vm_stat"]),
                             ("processes", ["/bin/ps", "-ww", "-axo", "pid=,ppid=,rss=,comm="])):
            try:
                output = self._run("health-" + key, command, timeout=10, public_output=False).decode("utf-8", errors="replace")
                if key == "processes":
                    # Never record argv/environment or unrelated process names.
                    output = "\n".join(line for line in output.splitlines()
                        if len(line.split(None, 3)) == 4 and re.search(
                            r"(?:CoreSimulator|Simulator\.app)(?:[./]|$)|"
                            r"(?:^|/)(?:launchd_sim|simctl|java|swift_worker|swiftc|swift-frontend|"
                            r"bazel(?:-[0-9][\w.-]*|\([^\r\n]*\))?)(?:$)",
                            line.split(None, 3)[3]))
                snapshot[key] = output[:32 * 1024]
            except (package.PackageError, OSError) as error:
                snapshot[key + "_error"] = str(error)
        try:
            devices = json.loads(self._run("health-devices", ["xcrun", "simctl", "list", "devices", "--json"],
                                           timeout=15, public_output=False))["devices"]
            states = {}
            for device_list in devices.values():
                for device in device_list:
                    state = device.get("state", "Unknown")
                    states[state] = states.get(state, 0) + 1
                    if device.get("udid", "").upper() == self.udid:
                        snapshot["owned_state"] = state
            snapshot["device_state_counts"] = states
        except (package.PackageError, OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            snapshot["device_state_error"] = str(error)
        self.evidence["health"].append(snapshot)
        self._save()

    def release_build_server(self, checkout):
        """Release only this hosted task's verified Bazel server before OS boot.

        Shutdown preserves on-disk build outputs/cache. Local and self-hosted
        workflows keep their server; no process discovery or global kill is used.
        """
        if self._owned or self.udid is not None:
            raise package.PackageError("Release the build server before creating the owned Simulator")
        record = {"status": "checking", "started_utc": utc_now()}
        self.evidence["build_server_release"] = record
        if (os.environ.get("GITHUB_ACTIONS") != "true"
                or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"):
            record.update(status="skipped", reason="not_github_hosted")
            self._save()
            return
        try:
            runner_temp = os.environ.get("RUNNER_TEMP")
            if not runner_temp or not Path(runner_temp).is_absolute():
                raise package.PackageError("Hosted build-server release requires absolute RUNNER_TEMP")
            temporary = Path(runner_temp).resolve(strict=True)
            checkout = Path(checkout).resolve(strict=True)
            if any(path == temporary or not path.is_relative_to(temporary)
                   for path in (checkout, self.artifacts)):
                raise package.PackageError("Build-server checkout and artifacts must be inside RUNNER_TEMP")
            # Make.BazelLocation derives this exact filename from versions.json;
            # require the independently pinned package version and file bytes.
            pin = package.load_json(package.ROOT / "Integration/BASE.json")["versions"]["bazel"]
            if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+:[0-9a-f]{64}", pin):
                raise package.PackageError("Invalid pinned Bazel version/hash")
            if package.load_json(checkout / "versions.json").get("bazel") != pin:
                raise package.PackageError("Checkout Bazel version differs from the package pin")
            version, expected_hash = pin.split(":")
            binary = package.bounded(checkout, f"build-input/bazel-{version}-darwin-arm64")
            hasher = hashlib.sha256()
            with binary.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    hasher.update(block)
            if hasher.hexdigest() != expected_hash:
                raise package.PackageError("Build-server Bazel binary differs from the pinned SHA-256")
            # Query the same workspace/startup configuration as make_command.
            # A busy server returns 9 instead of waiting or interrupting work.
            info = self._run("bazel-identity", [binary, "--noblock_for_lock", "info",
                "workspace", "output_base"], timeout=30, public_output=False, cwd=checkout).decode("utf-8")
            fields = {}
            for line in info.splitlines():
                key, separator, value = line.partition(": ")
                if separator and key in ("workspace", "output_base"):
                    if key in fields or not Path(value).is_absolute():
                        raise package.PackageError("Ambiguous Bazel server identity")
                    fields[key] = Path(value).resolve(strict=True)
            if set(fields) != {"workspace", "output_base"} or fields["workspace"] != checkout:
                raise package.PackageError("Bazel server belongs to a different or unknown workspace")
            # Cross-check the real outputs from the completed build, without a
            # glob, stale archive fallback, or selecting another workspace.
            expected_outputs = fields["output_base"] / "execroot/_main/bazel-out"
            outputs = (checkout / "bazel-out").resolve(strict=True)
            if outputs != expected_outputs or expected_outputs.resolve(strict=True) != expected_outputs:
                raise package.PackageError("Bazel output base does not own this checkout's built outputs")
            record.update(binary=binary.name, binary_sha256=expected_hash,
                          workspace=str(checkout), output_base=str(fields["output_base"]))
            self.health("before-build-server-release")
            try:
                self._run("bazel-shutdown", [binary, "--noblock_for_lock",
                    "--output_base=" + str(fields["output_base"]), "shutdown"], timeout=60, cwd=checkout)
            finally:
                self.health("after-build-server-release")
            record["status"] = "passed"
        except (package.PackageError, OSError, ValueError, KeyError, TypeError) as error:
            record.update(status="failed", error=str(error))
            if isinstance(error, package.PackageError):
                raise
            raise package.PackageError("Hosted build-server release failed: " + str(error)) from error
        finally:
            record["finished_utc"] = utc_now()
            self._save()

    def create(self):
        try:
            return self._create()
        except (ValueError, KeyError, TypeError, AttributeError) as error:
            raise package.PackageError("Simulator discovery/create returned invalid identity data") from error

    def _create(self):
        if self._owned or self.udid is not None:
            raise package.PackageError("SimulatorSession cannot replace an existing owned device")
        runtimes = json.loads(self._run("runtimes", ["xcrun", "simctl", "list", "runtimes", "--json"]))["runtimes"]
        runtime = [item for item in runtimes if item.get("isAvailable") and item.get("version") == self.os_version
                   and item.get("name", "").startswith("iOS ")]
        types = json.loads(self._run("device-types", ["xcrun", "simctl", "list", "devicetypes", "--json"]))["devicetypes"]
        device = [item for item in types if item.get("name") == self.device_type]
        if len(runtime) != 1 or len(device) != 1:
            raise package.PackageError("The exact Simulator device/runtime is unavailable or ambiguous")
        self.runtime = runtime[0]["identifier"]
        result = self._run("create", ["xcrun", "simctl", "create", "Draft Pocket " + self.label + " " + str(uuid.uuid4()),
                                      device[0]["identifier"], self.runtime]).decode().strip()
        self.udid = str(uuid.UUID(result)).upper()
        self._owned = True
        self.evidence.update({"udid": self.udid, "runtime": self.runtime})
        self._save()
        return self.udid

    def _require_owned(self):
        if not self._owned or not self.udid:
            raise package.PackageError("No Simulator created by this session is owned")

    def prepare_framebuffer(self):
        self._require_owned()
        policy = self._run("renderer-policy", ["defaults", "read", "com.apple.CoreSimulator",
            "FramebufferServerRendererPolicy"], timeout=10, allowed_codes=(0, 1)).decode("utf-8", errors="replace").strip()
        self.evidence["framebuffer_policy"] = policy if policy == "none" else "not_disabled_or_unset"
        if policy == "none":
            runner_temp = os.environ.get("RUNNER_TEMP")
            hosted = (os.environ.get("GITHUB_ACTIONS") == "true"
                      and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
                      and runner_temp and self.artifacts.is_relative_to(Path(runner_temp).resolve()))
            if not hosted:
                raise package.PackageError("Framebuffer rendering is disabled; no local host preference was changed")
            # Apple documents 'none' as disabling screenshots/recording. Restore
            # this exact prior value after using the ephemeral hosted runner.
            self._restore_renderer = True
            self._run("enable-renderer", ["defaults", "delete", "com.apple.CoreSimulator", "FramebufferServerRendererPolicy"], timeout=10)
        self._save()

    def boot(self):
        self._require_owned()
        self.health("before-boot")
        try:
            self._run("boot", ["xcrun", "simctl", "boot", self.udid], timeout=30)
            # Separate OS startup from XCTest's execution budget; this replaces
            # the old unbounded bootstatus call, not the recorder's ready guard.
            self._run("bootstatus", ["xcrun", "simctl", "bootstatus", self.udid, "-b"], timeout=180)
        finally:
            self.health("after-boot")

    def probe_framebuffer(self):
        self._require_owned()
        destination = self.artifacts / "simulator-framebuffer.png"
        self._run("framebuffer", ["xcrun", "simctl", "io", self.udid, "screenshot", "--type=png", destination], timeout=30)
        if not destination.is_file() or not 24 <= destination.stat().st_size <= 32 * 1024 ** 2:
            raise package.PackageError("Simulator did not produce a bounded framebuffer PNG")
        header = destination.read_bytes()[:24]
        if header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
            raise package.PackageError("Simulator framebuffer has no valid PNG header")
        width, height = int.from_bytes(header[16:20], "big"), int.from_bytes(header[20:24], "big")
        if not 100 <= width <= 10000 or not 100 <= height <= 10000:
            raise package.PackageError("Simulator framebuffer dimensions are invalid")
        pixels = self._run("framebuffer-decode", ["ffmpeg", "-v", "error", "-i", destination,
            "-frames:v", "1", "-vf", "scale=16:16", "-pix_fmt", "gray", "-f", "rawvideo", "pipe:1"],
            timeout=15, public_output=False)
        if len(pixels) != 256 or max(pixels) - min(pixels) < 8:
            raise package.PackageError("Simulator framebuffer is undecodable or blank after boot")
        self.evidence["framebuffer"] = {"file": destination.name, "width": width, "height": height,
            "decoded_gray_range": max(pixels) - min(pixels),
            "sha256": package.digest(destination.read_bytes()), "observed_utc": utc_now()}
        self._save()
        return destination

    def set_capture_status_bar(self):
        self._require_owned()
        self._run("status-bar", ["xcrun", "simctl", "status_bar", self.udid, "override",
            "--time", "9:41", "--batteryState", "charged", "--batteryLevel", "100"], timeout=15)

    def _shutdown(self, name, *, cleanup=False):
        self._require_owned()
        try:
            devices = json.loads(self._run(name + "-state", ["xcrun", "simctl", "list", "devices", "--json"],
                                           timeout=15, public_output=False, cleanup=cleanup))["devices"]
            owned = [item for items in devices.values() for item in items if item.get("udid", "").upper() == self.udid]
            if len(owned) != 1:
                raise package.PackageError("Owned Simulator is missing or ambiguous during shutdown")
            if owned[0].get("state") != "Shutdown":
                self._run(name, ["xcrun", "simctl", "shutdown", self.udid], timeout=30, cleanup=cleanup)
        except (ValueError, KeyError, TypeError, AttributeError) as error:
            raise package.PackageError("Simulator shutdown received invalid device-state data") from error

    def reset_after_failure(self):
        self._require_owned()
        self._shutdown("reset-shutdown")
        self.boot()

    def cleanup(self):
        errors = []
        self._cleanup_evidence_errors = []
        if self._owned:
            try:
                self.health("before-cleanup")
            except (package.PackageError, OSError) as error:
                errors.append("cleanup health evidence: " + str(error))
            # Query only our state, then avoid shutdown's error for an already
            # shut-down device. The raw inventory is never retained in artifacts.
            try:
                self._shutdown("shutdown", cleanup=True)
            except (package.PackageError, OSError, ValueError, KeyError, TypeError) as error:
                errors.append(str(error))
            try:
                self._run("delete", ["xcrun", "simctl", "delete", self.udid], timeout=30, cleanup=True)
                self._owned = False
            except (package.PackageError, OSError) as error:
                errors.append(str(error))
        if self._restore_renderer:
            try:
                self._run("restore-renderer", ["defaults", "write", "com.apple.CoreSimulator",
                    "FramebufferServerRendererPolicy", "-string", "none"], timeout=10, cleanup=True)
                self._restore_renderer = False
            except (package.PackageError, OSError) as error:
                errors.append(str(error))
        errors.extend(self._cleanup_evidence_errors)
        self.evidence["cleanup_errors"] = errors
        try:
            self._save()
        except OSError as error:
            errors.append("final cleanup evidence: " + str(error))
        if errors:
            raise package.PackageError("Owned Simulator cleanup failed: " + "; ".join(errors))
