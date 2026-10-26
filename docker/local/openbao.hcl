ui = true
disable_mlock = true
api_addr = "https://openbao:8200"
storage "file" { path = "/bao/data" }
listener "tcp" {
  address = "0.0.0.0:8200"
  tls_cert_file = "/tls/server.crt"
  tls_key_file = "/tls/bao.key"
}
