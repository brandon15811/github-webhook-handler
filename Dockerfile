FROM ubuntu:14.04
#TODO: Add python dev (maybe install and uninstall on the same line)
RUN apt-get update && apt-get --no-install-recommends -y install python python-pip git ca-certificates

RUN git clone https://github.com/brandon15811/github-webhook-handler.git /webhook
RUN cd /webhook && pip install --upgrade -r requirements.txt && pip install docker-py pymongo

ENV USE_PROXYFIX true
EXPOSE 8080/tcp
WORKDIR /webhook

RUN groupadd --gid 2000 python
RUN adduser --disabled-password --uid 2000 --gid 2000 --gecos "" python
RUN groupadd -g 999 docker
RUN usermod -aG docker python
USER python

CMD python index.py 8080
