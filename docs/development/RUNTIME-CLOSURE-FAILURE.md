# Remaining candidate-selection blocker

Status: exact semantic proofs are not yet green; affected gaps remain in progress.

```text

$ cmake -S . -B build/candidate-proof -G Ninja -DCMAKE_BUILD_TYPE=Debug -DBUILD_TESTING=ON -DHEPTA_INSTALL_RUNTIME=ON -DHEPTA_ENABLE_IBAPI=OFF
-- The CXX compiler identification is GNU 13.3.0
-- Detecting CXX compiler ABI info
-- Detecting CXX compiler ABI info - done
-- Check for working CXX compiler: /usr/bin/c++ - skipped
-- Detecting CXX compile features
-- Detecting CXX compile features - done
-- Found OpenSSL: /usr/lib/x86_64-linux-gnu/libcrypto.so (found version "3.0.13")
-- Performing Test CMAKE_HAVE_LIBC_PTHREAD
-- Performing Test CMAKE_HAVE_LIBC_PTHREAD - Success
-- Found Threads: TRUE
-- Configuring done (2.1s)
-- Generating done (0.1s)
-- Build files have been written to: /home/runner/work/heptatrader/heptatrader/build/candidate-proof


$ cmake --build build/candidate-proof --parallel 2
[1/186] Building CXX object HeptaTrade/CMakeFiles/hepta_execution_contract.dir/execution/execution_event_feed.cpp.o
[2/186] Building CXX object HeptaTrade/CMakeFiles/hepta_execution_contract.dir/execution/execution_service_protocol.cpp.o
[3/186] Building CXX object HeptaTrade/CMakeFiles/hepta_execution_transport.dir/execution/unix_execution_service_transport.cpp.o
[4/186] Building CXX object HeptaTrade/CMakeFiles/hepta_execution_transport.dir/execution/execution_event_feed_transport.cpp.o
[5/186] Building CXX object HeptaTrade/CMakeFiles/hepta_observability_core.dir/observability/runtime_telemetry.cpp.o
[6/186] Linking CXX static library HeptaTrade/libhepta_execution_transport.a
[7/186] Linking CXX static library HeptaTrade/libhepta_execution_contract.a
[8/186] Building CXX object HeptaTrade/CMakeFiles/hepta_execution_client.dir/execution/unix_execution_service_client.cpp.o
[9/186] Building CXX object HeptaTrade/CMakeFiles/hepta_execution_client.dir/execution/execution_event_feed_client.cpp.o
[10/186] Building CXX object HeptaTrade/CMakeFiles/hepta_execution_client.dir/execution/unix_execution_service_flatten_client.cpp.o
[11/186] Building CXX object HeptaTrade/CMakeFiles/hepta_execution_server.dir/execution/execution_decision_lease_authority.cpp.o
[12/186] Building CXX object HeptaTrade/CMakeFiles/hepta_execution_server.dir/execution/execution_event_feed_server.cpp.o
[13/186] Building CXX object HeptaTrade/CMakeFiles/hepta_execution_server.dir/execution/unix_execution_service.cpp.o
[14/186] Building CXX object HeptaTrade/CMakeFiles/hepta_execution_server.dir/execution/unix_execution_service_flatten.cpp.o
[15/186] Building CXX object HeptaTrade/CMakeFiles/hepta_agent_execution_support.dir/agent/decision_lease_manager.cpp.o
[16/186] Building CXX object HeptaTrade/CMakeFiles/hepta_execution_server.dir/execution/unix_execution_service_flatten_permit.cpp.o
[17/186] Linking CXX static library HeptaTrade/libhepta_observability_core.a
[18/186] Building CXX object HeptaTrade/CMakeFiles/hepta_risk_core.dir/risk/deterministic_risk_policy.cpp.o
[19/186] Building CXX object HeptaTrade/CMakeFiles/hepta_agent_execution_support.dir/events/execution_event_hub.cpp.o
[20/186] Building CXX object HeptaTrade/CMakeFiles/hepta_trading_tool_core.dir/state/ib_contract_identity.cpp.o
[21/186] Building CXX object HeptaTrade/CMakeFiles/hepta_trading_tool_core.dir/intent/bounded_json.cpp.o
[22/186] Building CXX object HeptaTrade/CMakeFiles/hepta_trading_tool_core.dir/intent/target_position_intent.cpp.o
[23/186] Building CXX object HeptaTrade/CMakeFiles/hepta_trading_tool_core.dir/intent/authoritative_decision_snapshot.cpp.o
[24/186] Building CXX object HeptaTrade/CMakeFiles/hepta_sessionctl.dir/cli/hepta_sessionctl.cpp.o
[25/186] Building CXX object HeptaTrade/CMakeFiles/hepta_sessionctl.dir/cli/hepta_sessionctl_command.cpp.o
[26/186] Building CXX object HeptaTrade/CMakeFiles/hepta_sessionctl.dir/cli/hepta_sessionctl_terminal_cleanup.cpp.o
[27/186] Building CXX object HeptaTrade/CMakeFiles/hepta_trading_tool_core.dir/tools/trading_tool_registry.cpp.o
[28/186] Building CXX object HeptaTrade/CMakeFiles/hepta_sessionctl.dir/tool_host/unix_session_supervisor_client.cpp.o
[29/186] Building CXX object HeptaTrade/CMakeFiles/hepta_sessionctl.dir/tool_host/session_supervisor_protocol.cpp.o
[30/186] Linking CXX static library HeptaTrade/libhepta_execution_client.a
[31/186] Linking CXX static library HeptaTrade/libhepta_execution_server.a
[32/186] Linking CXX static library HeptaTrade/libhepta_agent_execution_support.a
[33/186] Building CXX object HeptaTrade/CMakeFiles/hepta_sessionctl.dir/tool_host/session_supervisor_lease_store.cpp.o
[34/186] Linking CXX static library HeptaTrade/libhepta_risk_core.a
[35/186] Building CXX object HeptaTrade/CMakeFiles/hepta_portfolio_core.dir/portfolio/portfolio_compiler.cpp.o
[36/186] Building CXX object HeptaTrade/CMakeFiles/hepta_execution_core.dir/execution/execution_coordinator_cancel.cpp.o
[37/186] Building CXX object HeptaTrade/CMakeFiles/hepta_execution_core.dir/execution/execution_coordinator.cpp.o
[38/186] Building CXX object HeptaTrade/CMakeFiles/hepta_execution_core.dir/execution/execution_coordinator_recovery.cpp.o
[39/186] Building CXX object HeptaTrade/CMakeFiles/hepta_execution_core.dir/execution/execution_coordinator_reconnect.cpp.o
[40/186] Building CXX object HeptaTrade/CMakeFiles/hepta_execution_core.dir/execution/execution_coordinator_terminal.cpp.o
[41/186] Building CXX object HeptaTrade/CMakeFiles/hepta_execution_core.dir/execution/execution_place_order_dispatch.cpp.o
[42/186] Building CXX object HeptaTrade/CMakeFiles/hepta_execution_core.dir/execution/paper_terminal_mutation_manifest.cpp.o
[43/186] Building CXX object HeptaTrade/CMakeFiles/hepta_execution_core.dir/execution/execution_authoritative_flatten.cpp.o
[44/186] Building CXX object HeptaTrade/CMakeFiles/hepta_execution_core.dir/execution/execution_authoritative_flatten_dispatch.cpp.o
[45/186] Linking CXX static library HeptaTrade/libhepta_trading_tool_core.a
[46/186] Building CXX object HeptaTrade/CMakeFiles/hepta_execution_core.dir/oms_journal.cpp.o
[47/186] Building CXX object HeptaTrade/CMakeFiles/hepta_agent_os_core.dir/tool_host/tool_gateway_runtime_composition.cpp.o
[48/186] Building CXX object HeptaTrade/CMakeFiles/hepta_agent_os_core.dir/tool_host/tool_gateway_session_policy.cpp.o
[49/186] Building CXX object HeptaTrade/CMakeFiles/hepta_agent_os_core.dir/tool_host/agent_os_runtime_config.cpp.o
[50/186] Building CXX object HeptaTrade/CMakeFiles/hepta_agent_os_core.dir/tool_host/agent_os_runtime_composition.cpp.o
[51/186] Building CXX object HeptaTrade/CMakeFiles/hepta_agent_os_core.dir/tool_host/execution_gateway_runtime_config.cpp.o
[52/186] Building CXX object HeptaTrade/CMakeFiles/hepta_agent_os_core.dir/tool_host/execution_gateway_runtime_composition.cpp.o
[53/186] Building CXX object HeptaTrade/CMakeFiles/hepta_agent_os_core.dir/tool_host/execution_event_relay.cpp.o
[54/186] Building CXX object HeptaTrade/CMakeFiles/hepta_agent_os_core.dir/tool_host/session_supervisor_audit_journal.cpp.o
[55/186] Building CXX object HeptaTrade/CMakeFiles/hepta_agent_os_core.dir/tool_host/session_supervisor_protocol.cpp.o
[56/186] Building CXX object HeptaTrade/CMakeFiles/hepta_agent_os_core.dir/tool_host/tool_decision_audit.cpp.o
[57/186] Building CXX object HeptaTrade/CMakeFiles/hepta_agent_os_core.dir/tool_host/session_supervisor_lease_store.cpp.o
[58/186] Building CXX object HeptaTrade/CMakeFiles/hepta_agent_os_core.dir/tool_host/trading_tool_host_terminal.cpp.o
[59/186] Building CXX object HeptaTrade/CMakeFiles/hepta_agent_os_core.dir/tool_host/trading_tool_host.cpp.o
[60/186] Building CXX object HeptaTrade/CMakeFiles/hepta_agent_os_core.dir/tool_host/trading_tool_session_lifecycle.cpp.o
[61/186] Building CXX object HeptaTrade/CMakeFiles/hepta_agent_os_core.dir/tool_host/trading_tool_session_recovery.cpp.o
[62/186] Building CXX object HeptaTrade/CMakeFiles/hepta_agent_os_core.dir/tool_host/trading_tool_session_catalog.cpp.o
[63/186] Building CXX object HeptaTrade/CMakeFiles/hepta_agent_os_core.dir/tool_host/trading_tool_session_control_plane.cpp.o
[64/186] Building CXX object HeptaTrade/CMakeFiles/hepta_agent_os_core.dir/tool_host/typed_tool_framing.cpp.o
[65/186] Building CXX object HeptaTrade/CMakeFiles/hepta_agent_os_core.dir/tool_host/trading_tool_watch_transaction.cpp.o
[66/186] Building CXX object HeptaTrade/CMakeFiles/hepta_agent_os_core.dir/tool_host/typed_tool_result_codec.cpp.o
[67/186] Building CXX object HeptaTrade/CMakeFiles/hepta_agent_os_core.dir/tool_host/typed_tool_protocol.cpp.o
[68/186] Building CXX object HeptaTrade/CMakeFiles/hepta_agent_os_core.dir/tool_host/unix_session_supervisor_server.cpp.o
[69/186] Building CXX object HeptaTrade/CMakeFiles/hepta_agent_os_core.dir/tool_host/unix_tool_server.cpp.o
[70/186] Building CXX object HeptaTrade/CMakeFiles/hepta_native_tool_client.dir/client/native_tool_discovery_contract.cpp.o
[71/186] Building CXX object HeptaTrade/CMakeFiles/hepta_native_tool_client.dir/client/native_tool_client.cpp.o
[72/186] Building CXX object HeptaTrade/CMakeFiles/hepta_native_tool_client.dir/tool_host/unix_tool_client.cpp.o
[73/186] Building CXX object HeptaTrade/CMakeFiles/hepta_native_tool_client.dir/tool_host/typed_tool_framing.cpp.o
[74/186] Building CXX object HeptaTrade/CMakeFiles/hepta_native_tool_client.dir/tool_host/typed_tool_protocol.cpp.o
[75/186] Linking CXX executable HeptaTrade/hepta-sessionctl
[76/186] Building CXX object HeptaTrade/CMakeFiles/hepta_native_tool_client.dir/tool_host/typed_tool_result_codec.cpp.o
[77/186] Linking CXX static library HeptaTrade/libhepta_portfolio_core.a
[78/186] Linking CXX static library HeptaTrade/libhepta_execution_core.a
[79/186] Linking CXX static library HeptaTrade/libhepta_native_tool_client.a
[80/186] Linking CXX static library HeptaTrade/libhepta_agent_os_core.a
[81/186] Building CXX object HeptaTrade/CMakeFiles/heptactl.dir/cli/heptactl.cpp.o
[82/186] Building CXX object HeptaTrade/CMakeFiles/heptactl.dir/cli/heptactl_command.cpp.o
[83/186] Building CXX object HeptaTrade/CMakeFiles/hepta_paper_terminal_latch_committer.dir/cli/hepta_paper_terminal_latch_committer.cpp.o
[84/186] Building CXX object HeptaTrade/CMakeFiles/heptactl.dir/cli/heptactl_exit_codes.cpp.o
[85/186] Building CXX object HeptaTrade/CMakeFiles/hepta_paper_terminal_latch_committer.dir/execution/paper_terminal_external_latch.cpp.o
[86/186] Building CXX object HeptaTrade/CMakeFiles/hepta_tool_gatewayd.dir/tool_host/hepta_tool_gatewayd.cpp.o
[87/186] Building CXX object HeptaTrade/CMakeFiles/hepta_executiond.dir/execution/hepta_executiond.cpp.o
[88/186] Building CXX object HeptaTrade/CMakeFiles/hepta_executiond.dir/execution/execution_service_runtime_config.cpp.o
[89/186] Building CXX object HeptaTrade/CMakeFiles/hepta_executiond.dir/simulator/deterministic_execution_venue.cpp.o
[90/186] Building CXX object HeptaTrade/CMakeFiles/hepta_executiond.dir/execution/execution_service_runtime_composition.cpp.o
[91/186] Building CXX object tests/CMakeFiles/hepta_trading_contract_tests.dir/trading_contract_tests.cpp.o
[92/186] Building CXX object tests/CMakeFiles/hepta_native_tool_client_tests.dir/native_tool_client_tests.cpp.o
[93/186] Building CXX object tests/CMakeFiles/hepta_execution_coordinator_tests.dir/__/HeptaTrade/oms_recover.cpp.o
[94/186] Building CXX object tests/CMakeFiles/hepta_oms_journal_durability_tests.dir/oms_journal_durability_tests.cpp.o
[95/186] Building CXX object tests/CMakeFiles/hepta_execution_coordinator_tests.dir/execution_coordinator_tests.cpp.o
[96/186] Building CXX object tests/CMakeFiles/hepta_oms_journal_durability_tests.dir/__/HeptaTrade/oms_journal.cpp.o
[97/186] Building CXX object tests/CMakeFiles/hepta_execution_event_hub_tests.dir/execution_event_hub_tests.cpp.o
[98/186] Building CXX object tests/CMakeFiles/hepta_trading_tool_registry_tests.dir/trading_tool_registry_tests.cpp.o
[99/186] Building CXX object tests/CMakeFiles/hepta_execution_event_hub_tests.dir/__/HeptaTrade/events/owner_scoped_health_publisher.cpp.o
[100/186] Building CXX object tests/CMakeFiles/hepta_execution_event_feed_tests.dir/__/HeptaTrade/tool_host/execution_event_relay.cpp.o
[101/186] Building CXX object tests/CMakeFiles/hepta_execution_event_feed_tests.dir/execution_event_feed_tests.cpp.o
[102/186] Building CXX object tests/CMakeFiles/hepta_execution_gateway_runtime_composition_tests.dir/execution_gateway_runtime_composition_tests.cpp.o
[103/186] Building CXX object tests/CMakeFiles/hepta_trading_tool_host_tests.dir/trading_tool_host_tests.cpp.o
[104/186] Building CXX object tests/CMakeFiles/hepta_unix_tool_server_tests.dir/__/HeptaTrade/tool_host/unix_tool_client.cpp.o
[105/186] Building CXX object tests/CMakeFiles/hepta_unix_tool_server_tests.dir/unix_tool_server_tests.cpp.o
[106/186] Building CXX object tests/CMakeFiles/hepta_session_supervisor_protocol_boundary_tests.dir/session_supervisor_protocol_boundary_tests.cpp.o
[107/186] Building CXX object tests/CMakeFiles/hepta_session_supervisor_lease_store_migration_tests.dir/session_supervisor_lease_store_migration_tests.cpp.o
[108/186] Building CXX object tests/CMakeFiles/hepta_unix_session_supervisor_server_tests.dir/unix_session_supervisor_server_tests.cpp.o
[109/186] Building CXX object tests/CMakeFiles/hepta_agent_simulator_e2e_tests.dir/agent_simulator_e2e_tests.cpp.o
[110/186] Building CXX object tests/CMakeFiles/hepta_agent_simulator_e2e_tests.dir/__/HeptaTrade/state/authoritative_trading_snapshot_store.cpp.o
[111/186] Building CXX object tests/CMakeFiles/hepta_agent_simulator_e2e_tests.dir/__/HeptaTrade/simulator/deterministic_execution_venue.cpp.o
[112/186] Building CXX object tests/CMakeFiles/hepta_decision_lease_manager_tests.dir/__/HeptaTrade/agent/decision_lease_manager.cpp.o
[113/186] Building CXX object tests/CMakeFiles/hepta_decision_lease_manager_tests.dir/decision_lease_manager_tests.cpp.o
[114/186] Building CXX object tests/CMakeFiles/hepta_execution_decision_lease_authority_tests.dir/execution_decision_lease_authority_tests.cpp.o
[115/186] Building CXX object tests/CMakeFiles/hepta_authoritative_trading_snapshot_store_tests.dir/authoritative_trading_snapshot_store_tests.cpp.o
[116/186] Building CXX object tests/CMakeFiles/hepta_snapshot_refresh_coordinator_tests.dir/snapshot_refresh_coordinator_tests.cpp.o
[117/186] Building CXX object tests/CMakeFiles/hepta_snapshot_refresh_coordinator_tests.dir/__/HeptaTrade/state/snapshot_refresh_coordinator.cpp.o
[118/186] Building CXX object tests/CMakeFiles/hepta_authoritative_trading_snapshot_store_tests.dir/__/HeptaTrade/state/authoritative_trading_snapshot_store.cpp.o
[119/186] Building CXX object tests/CMakeFiles/hepta_ib_order_lifecycle_tests.dir/ib_order_lifecycle_tests.cpp.o
[120/186] Building CXX object tests/CMakeFiles/hepta_ib_order_lifecycle_tests.dir/__/HeptaTrade/adapter_ib/ib_order_lifecycle.cpp.o
[121/186] Building CXX object tests/CMakeFiles/hepta_ib_gateway_adapter_risk_tests.dir/__/HeptaTrade/adapter_ib/ib_api_wrapper.cpp.o
[122/186] Building CXX object tests/CMakeFiles/hepta_ib_gateway_adapter_risk_tests.dir/ib_gateway_adapter_risk_tests.cpp.o
[123/186] Building CXX object tests/CMakeFiles/hepta_ib_gateway_adapter_risk_tests.dir/__/HeptaTrade/adapter_ib/ib_decimal_compat.cpp.o
[124/186] Building CXX object tests/CMakeFiles/hepta_ib_gateway_adapter_risk_tests.dir/__/HeptaTrade/adapter_ib/ib_gateway_adapter_event_state.cpp.o
[125/186] Building CXX object tests/CMakeFiles/hepta_ib_gateway_adapter_risk_tests.dir/__/HeptaTrade/adapter_ib/ib_gateway_adapter.cpp.o
[126/186] Building CXX object tests/CMakeFiles/hepta_ib_gateway_adapter_risk_tests.dir/__/HeptaTrade/adapter_ib/ib_gateway_adapter_terminal.cpp.o
[127/186] Building CXX object tests/CMakeFiles/hepta_ib_gateway_adapter_risk_tests.dir/__/HeptaTrade/adapter_ib/ib_gateway_adapter_order_submission.cpp.o
[128/186] Building CXX object tests/CMakeFiles/hepta_ib_gateway_adapter_risk_tests.dir/__/HeptaTrade/adapter_ib/ib_gateway_adapter_reduce_only.cpp.o
[129/186] Building CXX object tests/CMakeFiles/hepta_ib_gateway_adapter_risk_tests.dir/__/HeptaTrade/adapter_ib/ib_order_lifecycle.cpp.o
[130/186] Building CXX object tests/CMakeFiles/hepta_ib_gateway_adapter_risk_tests.dir/__/HeptaTrade/adapter_ib/ib_venue_correlation.cpp.o
[131/186] Building CXX object tests/CMakeFiles/hepta_ib_paper_kill_switch_tests.dir/ib_paper_kill_switch_tests.cpp.o
[132/186] Building CXX object tests/CMakeFiles/hepta_deterministic_risk_policy_tests.dir/deterministic_risk_policy_tests.cpp.o
[133/186] Building CXX object tests/CMakeFiles/hepta_ib_paper_kill_switch_tests.dir/__/HeptaTrade/execution/ib_paper_kill_switch.cpp.o
[134/186] Building CXX object tests/CMakeFiles/hepta_target_position_intent_tests.dir/target_position_intent_tests.cpp.o
[135/186] Building CXX object tests/CMakeFiles/hepta_target_position_intent_tests.dir/__/HeptaTrade/intent/target_position_intent.cpp.o
[136/186] Building CXX object tests/CMakeFiles/hepta_authoritative_decision_snapshot_tests.dir/authoritative_decision_snapshot_tests.cpp.o
[137/186] Building CXX object tests/CMakeFiles/hepta_authoritative_decision_snapshot_tests.dir/__/HeptaTrade/intent/authoritative_decision_snapshot.cpp.o
[138/186] Building CXX object tests/CMakeFiles/hepta_authoritative_decision_snapshot_tests.dir/__/HeptaTrade/intent/bounded_json.cpp.o
[139/186] Building CXX object tests/CMakeFiles/hepta_authoritative_decision_snapshot_tests.dir/__/HeptaTrade/intent/target_position_intent.cpp.o
[140/186] Building CXX object tests/CMakeFiles/hepta_unsupported_venue_adapter_tests.dir/__/HeptaTrade/adapter_ctp/ctp_gateway_adapter.cpp.o
[141/186] Building CXX object tests/CMakeFiles/hepta_unsupported_venue_adapter_tests.dir/unsupported_venue_adapter_tests.cpp.o
[142/186] Building CXX object tests/CMakeFiles/hepta_unsupported_venue_adapter_tests.dir/__/HeptaTrade/adapter_xt/xt_gateway_adapter.cpp.o
[143/186] Building CXX object tests/CMakeFiles/hepta_runtime_telemetry_tests.dir/runtime_telemetry_tests.cpp.o
[144/186] Building CXX object tests/CMakeFiles/hepta_execution_preview_permit_tests.dir/execution_preview_permit_tests.cpp.o
[145/186] Building CXX object tests/CMakeFiles/hepta_portfolio_compiler_tests.dir/portfolio_compiler_tests.cpp.o
[146/186] Building CXX object tests/CMakeFiles/hepta_oms_crash_replay_tests.dir/oms_crash_replay_tests.cpp.o
[147/186] Building CXX object tests/CMakeFiles/hepta_target_position_tool_tests.dir/target_position_tool_tests.cpp.o
[148/186] Building CXX object tests/CMakeFiles/hepta_protocol_fuzz_smoke_tests.dir/protocol_fuzz_smoke_tests.cpp.o
[149/186] Building CXX object tests/CMakeFiles/hepta_risk_latency_fixture_tests.dir/risk_latency_fixture_tests.cpp.o
[150/186] Linking CXX executable HeptaTrade/heptactl
[151/186] Linking CXX executable HeptaTrade/hepta-paper-terminal-latch-committer
[152/186] Building CXX object tests/CMakeFiles/hepta_oms_crash_replay_tests.dir/__/HeptaTrade/oms_journal.cpp.o
[153/186] Linking CXX executable HeptaTrade/hepta-executiond
[154/186] Linking CXX executable tests/hepta_trading_contract_tests
[155/186] Linking CXX executable tests/hepta_native_tool_client_tests
[156/186] Linking CXX executable HeptaTrade/hepta-tool-gatewayd
-- Gateway privileged-symbol boundary PASS: /home/runner/work/heptatrader/heptatrader/build/candidate-proof/HeptaTrade/hepta-tool-gatewayd; defined_symbols=8699 observed; Release-only quantitative budget not enforced
[157/186] Linking CXX executable tests/hepta_oms_journal_durability_tests
[158/186] Linking CXX executable tests/hepta_execution_coordinator_tests
[159/186] Linking CXX executable tests/hepta_execution_event_hub_tests
[160/186] Linking CXX executable tests/hepta_execution_event_feed_tests
[161/186] Linking CXX executable tests/hepta_trading_tool_registry_tests
[162/186] Linking CXX executable tests/hepta_execution_gateway_runtime_composition_tests
[163/186] Linking CXX executable tests/hepta_trading_tool_host_tests
[164/186] Linking CXX executable tests/hepta_unix_tool_server_tests
[165/186] Linking CXX executable tests/hepta_session_supervisor_protocol_boundary_tests
[166/186] Linking CXX executable tests/hepta_unix_session_supervisor_server_tests
[167/186] Linking CXX executable tests/hepta_session_supervisor_lease_store_migration_tests
[168/186] Linking CXX executable tests/hepta_decision_lease_manager_tests
[169/186] Linking CXX executable tests/hepta_execution_decision_lease_authority_tests
[170/186] Linking CXX executable tests/hepta_authoritative_trading_snapshot_store_tests
[171/186] Linking CXX executable tests/hepta_snapshot_refresh_coordinator_tests
[172/186] Linking CXX executable tests/hepta_ib_order_lifecycle_tests
[173/186] Linking CXX executable tests/hepta_agent_simulator_e2e_tests
[174/186] Linking CXX executable tests/hepta_ib_paper_kill_switch_tests
[175/186] Linking CXX executable tests/hepta_ib_gateway_adapter_risk_tests
[176/186] Linking CXX executable tests/hepta_deterministic_risk_policy_tests
[177/186] Linking CXX executable tests/hepta_target_position_intent_tests
[178/186] Linking CXX executable tests/hepta_authoritative_decision_snapshot_tests
[179/186] Linking CXX executable tests/hepta_unsupported_venue_adapter_tests
[180/186] Linking CXX executable tests/hepta_execution_preview_permit_tests
[181/186] Linking CXX executable tests/hepta_runtime_telemetry_tests
[182/186] Linking CXX executable tests/hepta_portfolio_compiler_tests
[183/186] Linking CXX executable tests/hepta_target_position_tool_tests
[184/186] Linking CXX executable tests/hepta_oms_crash_replay_tests
[185/186] Linking CXX executable tests/hepta_protocol_fuzz_smoke_tests
[186/186] Linking CXX executable tests/hepta_risk_latency_fixture_tests


$ ctest --test-dir build/candidate-proof -N
Internal ctest changing into directory: /home/runner/work/heptatrader/heptatrader/build/candidate-proof
Test project /home/runner/work/heptatrader/heptatrader/build/candidate-proof
  Test  #1: hepta_trading_contract_tests
  Test  #2: hepta_native_tool_client_tests
  Test  #3: hepta_execution_coordinator_tests
  Test  #4: hepta_oms_journal_durability_tests
  Test  #5: hepta_trading_tool_registry_tests
  Test  #6: hepta_execution_event_hub_tests
  Test  #7: hepta_execution_event_feed_tests
  Test  #8: hepta_execution_gateway_runtime_composition_tests
  Test  #9: hepta_trading_tool_host_tests
  Test #10: hepta_unix_tool_server_tests
  Test #11: hepta_unix_session_supervisor_server_tests
  Test #12: hepta_session_supervisor_protocol_boundary_tests
  Test #13: hepta_session_supervisor_lease_store_migration_tests
  Test #14: hepta_agent_simulator_e2e_tests
  Test #15: hepta_decision_lease_manager_tests
  Test #16: hepta_execution_decision_lease_authority_tests
  Test #17: hepta_authoritative_trading_snapshot_store_tests
  Test #18: hepta_snapshot_refresh_coordinator_tests
  Test #19: hepta_ib_order_lifecycle_tests
  Test #20: hepta_ib_gateway_adapter_risk_tests
  Test #21: hepta_ib_paper_kill_switch_tests
  Test #22: hepta_deterministic_risk_policy_tests
  Test #23: hepta_target_position_intent_tests
  Test #24: hepta_authoritative_decision_snapshot_tests
  Test #25: hepta_unsupported_venue_adapter_tests
  Test #26: hepta_execution_preview_permit_tests
  Test #27: hepta_runtime_telemetry_tests
  Test #28: hepta_target_position_tool_tests
  Test #29: hepta_portfolio_compiler_tests
  Test #30: hepta_oms_crash_replay_tests
  Test #31: hepta_protocol_fuzz_smoke_tests
  Test #32: hepta_risk_latency_fixture_tests

Total Tests: 32


$ ctest --test-dir build/candidate-proof --output-on-failure -R ^hepta_target_protocol_idempotency_tests$|^hepta_target_permit_lifecycle_tests$
Internal ctest changing into directory: /home/runner/work/heptatrader/heptatrader/build/candidate-proof
Test project /home/runner/work/heptatrader/heptatrader/build/candidate-proof
No tests were found!!!

catalog <HTTPError 410: 'Gone'>
candidate error RuntimeError("openai/gpt-4.1: <HTTPError 410: 'Gone'>;URLError(gaierror(-2, 'Name or service not known'))")
candidate error RuntimeError("openai/gpt-5-mini: <HTTPError 410: 'Gone'>;URLError(gaierror(-2, 'Name or service not known'))")
candidate error RuntimeError("openai/gpt-5: <HTTPError 410: 'Gone'>;URLError(gaierror(-2, 'Name or service not known'))")
candidate error RuntimeError("anthropic/claude-sonnet-4: <HTTPError 410: 'Gone'>;URLError(gaierror(-2, 'Name or service not known'))")
candidate error RuntimeError("google/gemini-2.5-pro: <HTTPError 410: 'Gone'>;URLError(gaierror(-2, 'Name or service not known'))")
candidate error RuntimeError("anthropic/claude-3.7-sonnet: <HTTPError 410: 'Gone'>;URLError(gaierror(-2, 'Name or service not known'))")
candidate error RuntimeError("xai/grok-3: <HTTPError 410: 'Gone'>;URLError(gaierror(-2, 'Name or service not known'))")
candidate error RuntimeError("openai/gpt-4o: <HTTPError 410: 'Gone'>;URLError(gaierror(-2, 'Name or service not known'))")
candidate error RuntimeError("deepseek/deepseek-v3-0324: <HTTPError 410: 'Gone'>;URLError(gaierror(-2, 'Name or service not known'))")
catalog <HTTPError 410: 'Gone'>
candidate error RuntimeError("openai/gpt-5: <HTTPError 410: 'Gone'>;URLError(gaierror(-2, 'Name or service not known'))")
candidate error RuntimeError("anthropic/claude-sonnet-4: <HTTPError 410: 'Gone'>;URLError(gaierror(-2, 'Name or service not known'))")
candidate error RuntimeError("openai/gpt-4.1: <HTTPError 410: 'Gone'>;URLError(gaierror(-2, 'Name or service not known'))")
candidate error RuntimeError("openai/gpt-5-mini: <HTTPError 410: 'Gone'>;URLError(gaierror(-2, 'Name or service not known'))")
candidate error RuntimeError("anthropic/claude-3.7-sonnet: <HTTPError 410: 'Gone'>;URLError(gaierror(-2, 'Name or service not known'))")
candidate error RuntimeError("google/gemini-2.5-pro: <HTTPError 410: 'Gone'>;URLError(gaierror(-2, 'Name or service not known'))")
candidate error RuntimeError("xai/grok-3: <HTTPError 429: 'Too Many Requests'>;URLError(gaierror(-2, 'Name or service not known'))")
candidate error RuntimeError("deepseek/deepseek-v3-0324: <HTTPError 429: 'Too Many Requests'>;URLError(gaierror(-2, 'Name or service not known'))")
candidate error RuntimeError("openai/gpt-4o: <HTTPError 429: 'Too Many Requests'>;URLError(gaierror(-2, 'Name or service not known'))")
catalog <HTTPError 429: 'Too Many Requests'>
candidate error RuntimeError("anthropic/claude-3.7-sonnet: <HTTPError 429: 'Too Many Requests'>;URLError(gaierror(-2, 'Name or service not known'))")
candidate error RuntimeError("openai/gpt-5: <HTTPError 429: 'Too Many Requests'>;URLError(gaierror(-2, 'Name or service not known'))")
candidate error RuntimeError("openai/gpt-5-mini: <HTTPError 429: 'Too Many Requests'>;URLError(gaierror(-2, 'Name or service not known'))")
candidate error RuntimeError("openai/gpt-4.1: <HTTPError 429: 'Too Many Requests'>;URLError(gaierror(-2, 'Name or service not known'))")
candidate error RuntimeError("anthropic/claude-sonnet-4: <HTTPError 429: 'Too Many Requests'>;URLError(gaierror(-2, 'Name or service not known'))")
candidate error RuntimeError("google/gemini-2.5-pro: <HTTPError 429: 'Too Many Requests'>;URLError(gaierror(-2, 'Name or service not known'))")
candidate error RuntimeError("xai/grok-3: <HTTPError 429: 'Too Many Requests'>;URLError(gaierror(-2, 'Name or service not known'))")
candidate error RuntimeError("openai/gpt-4o: <HTTPError 429: 'Too Many Requests'>;URLError(gaierror(-2, 'Name or service not known'))")
candidate error RuntimeError("deepseek/deepseek-v3-0324: <HTTPError 429: 'Too Many Requests'>;URLError(gaierror(-2, 'Name or service not known'))")
catalog <HTTPError 429: 'Too Many Requests'>
candidate error RuntimeError("openai/gpt-5: <HTTPError 429: 'Too Many Requests'>;URLError(gaierror(-2, 'Name or service not known'))")
candidate error RuntimeError("openai/gpt-5-mini: <HTTPError 429: 'Too Many Requests'>;URLError(gaierror(-2, 'Name or service not known'))")
candidate error RuntimeError("openai/gpt-4.1: <HTTPError 429: 'Too Many Requests'>;URLError(gaierror(-2, 'Name or service not known'))")
candidate error RuntimeError("anthropic/claude-3.7-sonnet: <HTTPError 429: 'Too Many Requests'>;URLError(gaierror(-2, 'Name or service not known'))")
candidate error RuntimeError("anthropic/claude-sonnet-4: <HTTPError 429: 'Too Many Requests'>;URLError(gaierror(-2, 'Name or service not known'))")
candidate error RuntimeError("google/gemini-2.5-pro: <HTTPError 429: 'Too Many Requests'>;URLError(gaierror(-2, 'Name or service not known'))")
candidate error RuntimeError("deepseek/deepseek-v3-0324: <HTTPError 429: 'Too Many Requests'>;URLError(gaierror(-2, 'Name or service not known'))")
candidate error RuntimeError("openai/gpt-4o: <HTTPError 429: 'Too Many Requests'>;URLError(gaierror(-2, 'Name or service not known'))")
candidate error RuntimeError("xai/grok-3: <HTTPError 429: 'Too Many Requests'>;URLError(gaierror(-2, 'Name or service not known'))")
catalog <HTTPError 429: 'Too Many Requests'>
candidate error RuntimeError("openai/gpt-5-mini: <HTTPError 429: 'Too Many Requests'>;URLError(gaierror(-2, 'Name or service not known'))")
candidate error RuntimeError("anthropic/claude-sonnet-4: <HTTPError 429: 'Too Many Requests'>;URLError(gaierror(-2, 'Name or service not known'))")
candidate error RuntimeError("anthropic/claude-3.7-sonnet: <HTTPError 429: 'Too Many Requests'>;URLError(gaierror(-2, 'Name or service not known'))")
candidate error RuntimeError("google/gemini-2.5-pro: <HTTPError 429: 'Too Many Requests'>;URLError(gaierror(-2, 'Name or service not known'))")
candidate error RuntimeError("openai/gpt-4.1: <HTTPError 429: 'Too Many Requests'>;URLError(gaierror(-2, 'Name or service not known'))")
candidate error RuntimeError("openai/gpt-5: <HTTPError 429: 'Too Many Requests'>;URLError(gaierror(-2, 'Name or service not known'))")
candidate error RuntimeError("deepseek/deepseek-v3-0324: <HTTPError 429: 'Too Many Requests'>;URLError(gaierror(-2, 'Name or service not known'))")
candidate error RuntimeError("openai/gpt-4o: <HTTPError 429: 'Too Many Requests'>;URLError(gaierror(-2, 'Name or service not known'))")
candidate error RuntimeError("xai/grok-3: <HTTPError 429: 'Too Many Requests'>;URLError(gaierror(-2, 'Name or service not known'))")
catalog <HTTPError 429: 'Too Many Requests'>
candidate error RuntimeError("openai/gpt-5: <HTTPError 429: 'Too Many Requests'>;URLError(gaierror(-2, 'Name or service not known'))")
candidate error RuntimeError("openai/gpt-5-mini: <HTTPError 410: 'Gone'>;URLError(gaierror(-2, 'Name or service not known'))")
candidate error RuntimeError("openai/gpt-4.1: <HTTPError 429: 'Too Many Requests'>;URLError(gaierror(-2, 'Name or service not known'))")
candidate error RuntimeError("anthropic/claude-sonnet-4: <HTTPError 429: 'Too Many Requests'>;URLError(gaierror(-2, 'Name or service not known'))")
candidate error RuntimeError("anthropic/claude-3.7-sonnet: <HTTPError 429: 'Too Many Requests'>;URLError(gaierror(-2, 'Name or service not known'))")
candidate error RuntimeError("google/gemini-2.5-pro: <HTTPError 429: 'Too Many Requests'>;URLError(gaierror(-2, 'Name or service not known'))")
candidate error RuntimeError("deepseek/deepseek-v3-0324: <HTTPError 429: 'Too Many Requests'>;URLError(gaierror(-2, 'Name or service not known'))")
candidate error RuntimeError("xai/grok-3: <HTTPError 429: 'Too Many Requests'>;URLError(gaierror(-2, 'Name or service not known'))")
candidate error RuntimeError("openai/gpt-4o: <HTTPError 429: 'Too Many Requests'>;URLError(gaierror(-2, 'Name or service not known'))")
```
