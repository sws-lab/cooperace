// Data race only if sizeof(long) == 8 (LP64): then both threads write arr[0].
// Under ILP32 (sizeof(long) == 4) thread t1 writes arr[1] and there is no race.
// Expected verdict for data_model LP64: false (data race); for ILP32: true.
#include <pthread.h>
int arr[2];
void *t1(void *a) { arr[sizeof(long) == 8 ? 0 : 1] = 1; return 0; }
void *t2(void *a) { arr[0] = 2; return 0; }
int main(void) {
  pthread_t x, y;
  pthread_create(&x, 0, t1, 0);
  pthread_create(&y, 0, t2, 0);
  pthread_join(x, 0);
  pthread_join(y, 0);
  return 0;
}
