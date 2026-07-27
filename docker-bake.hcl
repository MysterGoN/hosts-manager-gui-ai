group "linux" {
  targets = ["linux-amd64", "linux-arm64"]
}

target "linux-base" {
  context    = "."
  dockerfile = "Dockerfile"
  target     = "artifact"
}

target "linux-amd64" {
  inherits  = ["linux-base"]
  platforms = ["linux/amd64"]
  args = {
    PYTHON_IMAGE = "python:3.14-bookworm"
  }
  output    = ["type=local,dest=dist/docker/linux-amd64"]
}

target "linux-arm64" {
  inherits  = ["linux-base"]
  platforms = ["linux/arm64"]
  args = {
    PYTHON_IMAGE = "python:3.14-trixie"
  }
  output    = ["type=local,dest=dist/docker/linux-arm64"]
}
