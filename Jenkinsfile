pipeline {
    agent {
        dockerfile {
            args '--group-add=20 --group-add=46 --device-cgroup-rule="c 189:* rmw" -v /dev/bus/usb:/dev/bus/usb'
        }
    }
    stages {
        stage('Build (Host)') {
            steps {
                sh './ci-scripts/install-host.sh'
            }
        }
        stage('Build (Firmware)') {
            steps {
                sh './ci-scripts/install-firmware.sh'
            }
        }
        stage('Test') {
            steps {
                sh 'uhubctl'
                sh 'lsusb'
                sh 'uhubctl -l 3-1.3 -p 3 -a cycle'
                sh 'sleep 2s'
                sh 'uhubctl'
                sh 'lsusb'
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
