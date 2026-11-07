```
status after 43 min: 1572 of 1582 instances done
  worker 3: solving acopf/case20758_epigrids_rect for 2574 s, 1.5 GB
  worker 7: solving mittelmann/gasoil_3200 for 2573 s, 1.0 GB
  worker 14: solving cutest/EIGENBCO for 2573 s, 1.7 GB
  worker 4: solving acopf/case78484_epigrids_polar for 2570 s, 3.0 GB
  worker 5: solving acopf/case78484_epigrids_rect for 2570 s, 3.1 GB
  worker 6: solving cops/elec_1600 for 2570 s, 3.7 GB
  worker 32: solving cutest/PDE1 for 2526 s, 1.8 GB
  worker 20: solving cutest/TWOD for 1322 s, 3.6 GB
  worker 13: solving cutest/EIGENB for 1096 s, 1.5 GB
  worker 21: solving cutest/MSS3 for 324 s, 1.3 GB
  20 idle
  machine: 71.7 GB of memory free, load 12.3
```

### MadNLP on Intel_R_Xeon_R_Gold_6148_CPU_2.40GHz

- shin-compute-005: Intel(R) Xeon(R) Gold 6148 CPU @ 2.40GHz (40 cores, 80 threads), 123.3 GB of memory

**acopf** (solved, SGM in seconds; a failure counts as 7200 s)

| configuration | small | medium | large | total |
|---|---|---|---|---|
| default | 58 / 58, 1.61 | 50 / 51, 23.38 | 18 / 20, 136.04 | 126 / 129, 16.10 |

**cops** (solved, SGM in seconds; a failure counts as 7200 s)

| configuration | small | medium | large | total |
|---|---|---|---|---|
| default | 23 / 24, 6.80 | 21 / 21, 13.63 | 19 / 19, 36.95 | 63 / 64, 15.50 |

**cutest** (solved, SGM in seconds; a failure counts as 7200 s)

| configuration | small | medium | large | total |
|---|---|---|---|---|
| default | 1126 / 1266, 13.62 | 34 / 40, 100.86 | 23 / 27, 313.08 | 1183 / 1333, 16.08 |

**mittelmann** (solved, SGM in seconds; a failure counts as 7200 s)

| configuration | small | medium | large | total |
|---|---|---|---|---|
| default | 8 / 9, 25.47 | 14 / 15, 103.39 | 20 / 22, 159.09 | 42 / 46, 99.35 |

