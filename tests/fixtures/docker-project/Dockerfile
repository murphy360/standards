# The image ci.yml builds with test-docker.yml and image.yml. It takes one file from its build context and runs one
# command, so a build that used the wrong context, or an architecture that cannot run, fails.
FROM alpine:3.24
COPY test.sh /opt/fixture/test.sh
RUN uname -m > /opt/fixture/arch
