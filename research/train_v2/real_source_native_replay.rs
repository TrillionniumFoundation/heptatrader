//! Isolated copy-only retry/indeterminate ACK probe. Do not run against original.
#[path = "real_source_native_lab.rs"]
mod lab;
fn main(){
    let a:Vec<String>=std::env::args().collect();
    assert_eq!(a.len(),8,"public, packet, outcome, copied directory, gen evidence, obs evidence, mode");
    lab::replay_original_signed_test_packet(&a[1],&a[2],&a[3],&a[4],&a[5],&a[6],&a[7]);
}
