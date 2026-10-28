#!/bin/bash
# Build + sign the overlay APK. Needs ~/android (build-tools 34, platform 29, jdk).
export PATH=~/android/jdk/bin:$PATH; set -e; cd "$(dirname "$0")"; BT=~/android/build-tools/34.0.0; JAR=~/android/platforms/android-29/android.jar; J=~/android/jdk/bin
rm -rf build; mkdir -p build/classes
$BT/aapt2 link -o build/res.apk -I $JAR --manifest AndroidManifest.xml
$J/javac -source 8 -target 8 -bootclasspath $JAR -classpath $JAR:$BT/core-lambda-stubs.jar -d build/classes $(find src -name '*.java')
$BT/d8 --min-api 26 --lib $JAR --output build $(find build/classes -name '*.class')
cp build/res.apk build/unsigned.apk; python3 -c "import zipfile;zipfile.ZipFile(\"build/unsigned.apk\",\"a\").write(\"build/classes.dex\",\"classes.dex\")"
$BT/zipalign -f 4 build/unsigned.apk build/aligned.apk
PATH=$J:$PATH $BT/apksigner sign --ks /workspace/.ecal/kiosk.keystore --ks-pass file:/workspace/.ecal/kiosk.keystore.pass --out build/DoorbellOverlay.apk build/aligned.apk
echo built build/DoorbellOverlay.apk
