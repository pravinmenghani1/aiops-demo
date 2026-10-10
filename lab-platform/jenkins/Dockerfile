FROM jenkins/jenkins:lts-jdk21
# Plugins: Configuration as Code, Pipeline, SSH build agents
COPY --chown=jenkins:jenkins plugins.txt /usr/share/jenkins/ref/plugins.txt
RUN jenkins-plugin-cli -f /usr/share/jenkins/ref/plugins.txt
# The route-eta-service pipeline job, copied into JENKINS_HOME on first start
COPY --chown=jenkins:jenkins ref/ /usr/share/jenkins/ref/
RUN echo 2.0 > /usr/share/jenkins/ref/jenkins.install.UpgradeWizard.state
