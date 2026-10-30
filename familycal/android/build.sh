#!/bin/bash
# Build + sign FamilyCalendar.apk without Gradle. Needs ~/android (jdk17, build-tools 34, platform 29, kotlinc).
set -e; cd "$(dirname "$0")"
export JAVA_HOME=~/android/jdk PATH=~/android/jdk/bin:$PATH
BT=~/android/build-tools/34.0.0; JAR=~/android/platforms/android-29/android.jar; KC=~/android/kotlinc
STD=$KC/lib/kotlin-stdlib.jar
rm -rf build; mkdir -p build/classes build/dex
# Family settings come from ../config.local.json (gitignored) or ../config.example.json; FAMCAL_APP_CONFIG overrides
# the path and FAMCAL_URL the server URL. Generates build/gen/BuildCfg.kt and merges avatar PNGs into build/assets.
python3 gen_config.py build
$BT/aapt2 compile --dir res -o build/res.zip
$BT/aapt2 link -o build/res.apk -I $JAR --manifest AndroidManifest.xml -A build/assets -R build/res.zip --auto-add-overlay --min-sdk-version 26 --target-sdk-version 29
$KC/bin/kotlinc -nowarn -jvm-target 1.8 -no-reflect -cp $JAR -d build/classes $(find src build/gen -name '*.kt') 2>&1 | grep -v '^warning' || true
test -f build/classes/com/bakerk7/familycalendar/MainActivity.class
# R8 shrinks kotlin-stdlib down to what we use (smaller dex = less RAM on the tablet).
cat > build/keep.pro <<'K'
-keep class com.bakerk7.familycalendar.MainActivity { *; }
-keep class com.bakerk7.familycalendar.RemReceiver { *; }
-keep class com.bakerk7.familycalendar.SleepAdmin { *; }
-keep class com.bakerk7.familycalendar.WakeReceiver { *; }
-dontobfuscate
-dontwarn **
K
~/android/jdk/bin/jar cf build/app.jar -C build/classes .
java -cp $BT/lib/d8.jar com.android.tools.r8.R8 --release --min-api 26 --lib $JAR --pg-conf build/keep.pro \
  --output build/dex build/app.jar $STD
cp build/res.apk build/unsigned.apk
python3 -c "import zipfile;zipfile.ZipFile('build/unsigned.apk','a').write('build/dex/classes.dex','classes.dex')"
$BT/zipalign -f 4 build/unsigned.apk build/aligned.apk
. build/sign.env
$BT/apksigner sign --ks "$KS" --ks-pass "file:$KS_PASS" --out build/FamilyCalendar.apk build/aligned.apk
ls -la build/FamilyCalendar.apk
