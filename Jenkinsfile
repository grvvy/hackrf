pipeline {
    agent any
    stages {
        stage('Build Image') {
            steps {
                sh 'docker build . -t hackrf'
            }
        }
        stage('trusted_hil') {
            agent {
                docker {
                    image 'hackrf'
                    reuseNode true
                }
            }
            steps {
                dir('master') {
                    git url: 'https://github.com/grvvy/hackrf.git', branch:'master'
                    sh 'pwd'
                    sh 'ls -la'
                }
                sh 'pwd'
                sh 'ls -la'
                sh 'ls -la ci-scripts/'
                sh 'cp -r master/ci-scripts ci-scripts/'
                sh 'ls -la'
                sh 'ls -la ci-scripts/'
            }
        }
        stage ('Run') {
            agent {
                docker {
                    image 'hackrf'
                    reuseNode true
                    args '--group-add=20 --group-add=46 --device-cgroup-rule="c 189:* rmw" --device-cgroup-rule="c 166:* rmw" -v /dev/bus/usb:/dev/bus/usb'
                }
            }
            steps {
                sh 'env'
                sh 'sleep 180s'
            }
        }
    }
    post {
        always {
            cleanWs(cleanWhenNotBuilt: false,
                    deleteDirs: true,
                    disableDeferredWipeout: true,
                    notFailBuild: true)
        }
    }
}
