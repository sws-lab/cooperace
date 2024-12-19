// This file is part of the SV-Benchmarks collection of verification tasks:
// https://gitlab.com/sosy-lab/benchmarking/sv-benchmarks
//
// SPDX-FileCopyrightText: 2021 F. Schuessele <schuessf@informatik.uni-freiburg.de>
// SPDX-FileCopyrightText: 2021 D. Klumpp <klumpp@informatik.uni-freiburg.de>
//
// SPDX-License-Identifier: LicenseRef-BSD-3-Clause-Attribution-Vandikas

typedef unsigned long int pthread_t;

union pthread_attr_t
{
  char __size[36];
  long int __align;
};
typedef union pthread_attr_t pthread_attr_t;

extern void __assert_fail(const char *__assertion, const char *__file,
      unsigned int __line, const char *__function)
     __attribute__ ((__nothrow__ , __leaf__)) __attribute__ ((__noreturn__));
void reach_error() { __assert_fail("0", "popl20-more-queue-add-2-nl.wvr.c", 21, __extension__ __PRETTY_FUNCTION__); }
extern int pthread_create (pthread_t *__restrict __newthread,
      const pthread_attr_t *__restrict __attr,
      void *(*__start_routine) (void *),
      void *__restrict __arg) __attribute__ ((__nothrow__)) __attribute__ ((__nonnull__ (1, 3)));
extern int pthread_join (pthread_t __th, void **__thread_return);

typedef unsigned int size_t;
extern void *malloc (size_t __size) __attribute__ ((__nothrow__ , __leaf__)) __attribute__ ((__malloc__)) ;

extern int  __VERIFIER_nondet_int(void);
extern unsigned int  __VERIFIER_nondet_uint(void);
extern _Bool __VERIFIER_nondet_bool(void);
extern void __VERIFIER_atomic_begin(void);
extern void __VERIFIER_atomic_end(void);

extern void abort(void);
void assume_abort_if_not(int cond) {
  if(!cond) {abort();}
}

int* q;
int q_front, q_back, n, s, N;
unsigned int x, y, C;

int *create_fresh_int_array(int size);

void* thread1(void* _argptr) {
  for (int i=0; i<N; i++) {
    __VERIFIER_atomic_begin();
    assume_abort_if_not(q_back >= 0 && q_back < n);
    assume_abort_if_not(q[q_back] == 1);
    q_back++;
    __VERIFIER_atomic_end();
    x = x + C;
  }
  __VERIFIER_atomic_begin();
  assume_abort_if_not(q_back >= 0 && q_back < n);
  assume_abort_if_not(q[q_back] == 0);
  q_back++;
  __VERIFIER_atomic_end();
  x = x + C;

  return 0;
}

void* thread2(void* _argptr) {
  int j;
  s = 0;
  while(1) {
    __VERIFIER_atomic_begin();
    assume_abort_if_not(q_back > q_front && q_front >= 0 && q_front < n);
    j = q[q_front];
    q_front++;
    __VERIFIER_atomic_end();
    s = s + j;
    y = y + C;
    if (j == 0) {
      break;
    }
  }

  return 0;
}

int main() {
  pthread_t t1, t2;
  
  C = __VERIFIER_nondet_uint();
  N = __VERIFIER_nondet_int();
  n = __VERIFIER_nondet_int();
  q_front = __VERIFIER_nondet_int();
  q_back = q_front;
  q = create_fresh_int_array(n);
  
  assume_abort_if_not(N >= 0);
  
  // main method
  pthread_create(&t1, 0, thread1, 0);
  pthread_create(&t2, 0, thread2, 0);
  pthread_join(t1, 0);
  pthread_join(t2, 0);
  
  assume_abort_if_not(x != y || s != N);
  reach_error();

  return 0;
}

int *create_fresh_int_array(int size) {
  assume_abort_if_not(size >= 0);
  assume_abort_if_not(size <= (((size_t) 4294967295) / sizeof(int)));

  int* arr = (int*)malloc(sizeof(int) * (size_t)size);
  for (int i = 0; i < size; i++) {
    arr[i] = __VERIFIER_nondet_int();
  }
  return arr;
}
