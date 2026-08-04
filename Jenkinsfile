// Interner Docker-Build auf jenkins.cygnusnet.de, parallel zum
// GitHub-Actions-Build. Fuehrend bleibt GitHub; nach git.cygnusnet.de:tiqora
// wird von Hand gepusht, der gitolite-post-receive stoesst diesen Job an.
//
// Ergebnis: hub.cygnusnet.de/tiqora:<branch-latest|latest> und :<tag|rev>,
// auf einem Git-Tag zusaetzlich :stable. Nur amd64 -- anders als bei auzui
// laeuft tiqora auf keinem arm64-Host.
@Library("jenkins-pipelines") _

pipeline {
    agent {
      label 'docker-jenkins'
    }

    stages {
        stage('Checkout SCM') {
            steps {
                script {
                    checkout scm
                }
            }
        }
        stage('Build') {
            steps {
                script {
                    // Ohne diese Args baut das Dockerfile ein Image mit leerer
                    // Version und unvollstaendigen OCI-Labels; Versionsanzeige
                    // im Frontend und Update-Check haengen daran.
                    def describe = sh(
                        returnStdout: true,
                        script: 'git -C source describe --tags --always --first-parent',
                    ).trim() - ~/^v/
                    def sha = sh(returnStdout: true, script: 'git -C source rev-parse HEAD').trim()
                    def buildTime = sh(returnStdout: true, script: 'date -u +%Y-%m-%dT%H:%M:%SZ').trim()

                    dockerBuild.buildAndPush(["linux/amd64"], 'source', [
                        VITE_APP_VERSION: describe,
                        VITE_GIT_SHA: sha,
                        TIQORA_VERSION: describe,
                        TIQORA_GIT_SHA: sha,
                        TIQORA_BUILD_TIME: buildTime,
                    ])
                }
            }
        }
    }
}
