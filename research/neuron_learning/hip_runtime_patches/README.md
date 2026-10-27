# HIP runtime source backports

This directory records the source changes used to build a candidate HIP overlay
for the Pocket4 resident trainer. It contains no SDK binaries. Selecting an
overlay remains the responsibility of the resident runtime manifest and its
separate validation evidence.

## Kernel-handle validation and error propagation

`hip-kernel-handle-backport.patch` targets ROCm/rocm-systems commit
`2b22ab0195cc1461cd9abf3b969e9dd7c10af350`, the source pin declared by the installed
ROCm 7.14 SDK. The patch SHA-256 is
`4c28a7184d0c3e1cc9983b8df7901aebe10f1aecf165a89648d293412ebc0c3f`.
`manifest.json` records the exact SHA-256 of every input and output source file.

The pinned `ihipLaunchKernel` handles only three failed `StatCO::GetFunc` results
explicitly. Other failures fall through to a cast of the caller's `hostFunction`
pointer to `hipFunction_t`. That pointer can refer to executable host code rather
than an `amd::Kernel` object. Downstream code dereferences it as a kernel and then
as a symbol. The fallback also runs if a lookup reports success with a null
output handle.

The official [ROCm/rocm-systems #11580](https://github.com/ROCm/rocm-systems/pull/11580)
describes this invalid-pointer path and a crash at
`amd::Symbol::getDeviceKernel` → `deviceKernels_.find()`. It merged on 2026-09-15,
after the installed source pin. The source-level defect exists in the deployed
pin. Its stack matches the resident slot-5 failure, but the original failing
`GetFunc` return value and pointer were not captured. The matching stack alone
does not prove which lookup error occurred in that run.

### Upstream provenance and local scope

| Upstream change | Source used | Backported behavior |
| --- | --- | --- |
| [#11580](https://github.com/ROCm/rocm-systems/pull/11580) | Head `370b93da77623233ef695f3dfa2e9df039f7140a`; merge `210c1da6b9127472d378d3488cd9d3471b5b3289` | Runtime-produced handle registry; register at `Function::BuildKernel`, unregister before release; validate the fallback and dynamic handle entry points |
| [#10150](https://github.com/ROCm/rocm-systems/pull/10150) | Merge `e14bd0645af6178e3ae0fc0ca605d77636ae9cb8` | Propagate `DigestFatBinary` failures during function-attribute, global-variable and managed-variable lazy loading |

This is a compatible source backport, rather than a cherry-pick of the complete
upstream commits. It uses the **complete launch-resolution lambda present at
#11580's head**. A successful lookup uses the returned handle. An
`hipErrorInvalidSymbol` result can use the caller pointer only if that pointer
was registered as a runtime-produced handle. An unknown pointer is rejected
with `hipErrorInvalidDeviceFunction`. All other error codes are propagated.
The complete error-propagation behavior must not be attributed to #10150 alone;
that PR added a narrower check in its launch path.

The handle registry uses its own leaf mutex. Reusing the existing platform lock
would invert lock order when a code object creates or releases a kernel. The
mutex and registry are declared before `statCO_`, so they remain alive while
`statCO_` destroys its functions. The obsolete dynamic-only scanning helpers are
removed, and both module entry points use the same registry.

The existing queue-idle repair from
[#8113](https://github.com/ROCm/rocm-systems/pull/8113), commit
`22af9e98a3dad737d0a7b153c712622139e5a2e6`, remains required by the current overlay
lineage. This patch does not modify its `rocclr/device/rocm/rocvirtual.cpp` file.
The isolated build must verify that file is unchanged from its previously
validated source. No compiler, SDK, HSA, kpack, profiler or queue setting is
changed by this backport.

### Validation recorded with this source

The local CPU validation applied the patch with zero fuzz to all seven exact
input files and checked every resulting digest against `manifest.json`. It then
compiled the actual extracted old and new launch lambdas, with the actual new
registry methods and a stub for `StatCO::GetFunc`.

All 13 contract scenarios passed for the candidate: valid static lookup, valid
runtime handle, unregistered pointer, retired handle, three previously
propagated errors, five previously swallowed errors, and success with a null
output. Eight of these scenarios demonstrated the old code returning a false
success and the original caller pointer. This validates the branch and registry
contract without executing HIP or claiming GPU stability. The validation receipt
digest and extracted-source digests are recorded in `manifest.json`.

The library build and real runtime acceptance are separate evidence. A candidate
must be built in a new directory using the recorded SDK and CMake options, then
published through the existing immutable-overlay mechanism. Existing source,
build, release, configuration and manual-hold files must remain unchanged while
the candidate is prepared. A successful build or a small number of tensor
allocations does not establish acceptance of the resident training lifecycle.

To apply the source patch, first verify the input digests in `manifest.json` and
use a disposable copy of the pinned source with the existing queue-idle patch:

```sh
patch --batch --forward --fuzz=0 --dry-run -p1 -i /absolute/path/to/hip-kernel-handle-backport.patch
patch --batch --forward --fuzz=0 -p1 -i /absolute/path/to/hip-kernel-handle-backport.patch
```

Run both commands from the copied rocm-systems root and verify the output digests
before building. A source mismatch requires investigation; do not increase fuzz
or silently rebase this patch onto another SDK pin.

### Remaining attribution and runtime acceptance

An isolated replay should retain the original input and runtime pins and record
the result of `StatCO::GetFunc` before launch resolution: error code,
`hostFunction`, returned handle, `deviceId`, and the mapping that contains the
caller pointer. This distinguishes an unregistered host stub from a loader
failure, such as a failed fat-binary load. The backport rejects or propagates
those failures; it does not itself repair an underlying loader failure.

If the original selected binary faults again at library offset `0x688fb9`, its
disassembly identifies a bucket-array load, `mov (%rax,%r8,8),%rax`. At that
specific instruction, `RDI` still points to the `amd::Symbol`, `RAX` holds the
bucket array, `R8` holds the bucket index, and `R10` holds the bucket count.
Capture these registers, the effective address `RAX + 8 * R8`, the 56-byte map
header, the owning kernel and program, and all thread stacks. These offsets and
register meanings apply only to the recorded original binary SHA
`90073685abf8430b7a5b5af4d42f4032080cb8dd4ca40fab4002661b686dc785`;
resolve symbols again for a newly built candidate.

The upstream CLR source is MIT licensed; its license is reproduced in
`UPSTREAM_LICENSE.txt`.
