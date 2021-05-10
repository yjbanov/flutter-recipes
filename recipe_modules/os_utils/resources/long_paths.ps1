# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

$filesystemKey = "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem"

if (Get-ItemProperty -Path $filesystemKey -Name "LongPathsEnabled") {
  # Key is already set, if it's not 1, set it
  $longPathsEnabled = Get-ItemPropertyValue `
      -Path "$filesystemKey" `
      -Name "LongPathsEnabled"
  if (-not $longPathsEnabled) {
    echo "LongPathsEnabled is currently 0. Setting to 1"
    Set-ItemProperty `
      -Path "$filesystemKey" `
      -Name "LongPathsEnabled" `
      -Value 1 `
      -Force
  } else {
    echo "LongPathsEnabled is currently 1. Not modifying."
  }

} else {
  # LongPathsEnabled value is not yet present, set it
  echo "LongPathsEnabled is not yet set on machine; setting to 1."
  New-ItemProperty `
      -Path "$filesystemKey" `
      -Name "LongPathsEnabled" `
      -Value 1 `
      -PropertyType DWORD `
      -Force
}

# Echo the current value.
$longPathsEnabled = Get-ItemPropertyValue `
    -Path "$filesystemKey" `
    -Name "LongPathsEnabled"
echo "LongPathsEnabled is set to $longPathsEnabled"
