```
status after 44 min: 252 of 1582 instances done
  worker 4: solving mittelmann/qssp180 for 839 s, 3.6 GB
  worker 2: waiting mittelmann/robot_a for 789 s, 4.1 GB
  worker 3: solving cutest/A0NNDNIL for 134 s, 3.8 GB
  worker 5: waiting cutest/A0NNDNSL for 131 s, 3.8 GB
  machine: 71.7 GB of memory free, load 12.3
```

### cuMadNLP on Quadro_RTX_6000

- shin-compute-005: Intel(R) Xeon(R) Gold 6148 CPU @ 2.40GHz (40 cores, 80 threads), 123.3 GB of memory; Quadro RTX 6000, 23.5 GB, driver 13.5.0, CUDA runtime 13.4.0

**acopf** (solved, SGM in seconds; a failure counts as 7200 s)

| configuration | small | medium | large | total |
|---|---|---|---|---|
| default | 57 / 58, 2.38 | 49 / 51, 7.06 | 18 / 23, 49.47 | 124 / 132, 8.42 |

**cops** (solved, SGM in seconds; a failure counts as 7200 s)

| configuration | small | medium | large | total |
|---|---|---|---|---|
| default | 24 / 24, 1.29 | 21 / 21, 3.37 | 20 / 20, 5.11 | 65 / 65, 3.04 |

**cutest** (solved, SGM in seconds; a failure counts as 7200 s)

| configuration | small | medium | large | total |
|---|---|---|---|---|
| default | 8 / 8, 4.41 | 1 / 2, 260.83 | – | 9 / 10, 15.91 |

**mittelmann** (solved, SGM in seconds; a failure counts as 7200 s)

| configuration | small | medium | large | total |
|---|---|---|---|---|
| default | 8 / 9, 16.41 | 10 / 15, 128.90 | 18 / 21, 37.06 | 36 / 45, 50.14 |

